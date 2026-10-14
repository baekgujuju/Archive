#!/usr/bin/env python3
"""
v2/enrich_tools.py — GitHub 스크린샷 자동 AI툴 분류 (백엔드) v3.

요청: 대표님 (2026-10-14)
- X/웹글 스크린샷에서 GitHub 저장소를 자동 감지 → AI툴로 분류
- data/ai-tools.json에 tweetId 키로 저장
- 지문(fingerprint) 기반 캐시: image+text+analyzer_version
- 검증된 GitHub repo만 저장 (API로 실존+README+포크 확인)
- 모호하면 pending, 링크 발명 금지

v3 수정 (2026-10-14 리뷰 피드백):
1. URL 없는 GitHub-like 제목도 검색 (README distinctive phrase 비교)
2. 텍스트 URL도 README 기반 한국어 요약/분류
3. 일시적 오류 전파, 모호한 다중 매칭 거부, distinctive phrase 매칭
4. 지문에 text+image+analyzer_version 포함, 배치 단위 원자적 저장
5. 캐시 히트도 tools_data에 재적용, 지문 변경 시 stale 제거
6. manifest.ai_tools 업데이트
7. quoted.get(field) 버그 수정
8. dry_run은 retry 마커 미작성
9. source_url도 해시에 포함
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent
DATA_DIR = V2_ROOT / "data"
MEDIA_FULL = V2_ROOT / "media" / "full"
TOOLS_JSON = DATA_DIR / "ai-tools.json"
CACHE_JSON = DATA_DIR / "ai-tools-cache.json"
MANIFEST_JSON = DATA_DIR / "manifest.json"
RETRY_MARKER = V2_ROOT / "hooks" / ".retry-enrich"
PROGRESS_JSON = DATA_DIR / "ai-tools-progress.json"

GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.IGNORECASE)

CACHE_VERSION = 3
ANALYZER_VERSION = "codex-gpt-6.1-sol-v1"


def log(msg):
    print(f"[enrich_tools] {msg}", flush=True)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_str(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def fingerprint(image_path, post_text, source_url):
    """
    지문: image hash + text hash + source_url hash + analyzer version.
    텍스트나 URL 변경 시에도 무효화.
    """
    parts = [ANALYZER_VERSION]
    if image_path and Path(image_path).exists():
        parts.append(f"img:{sha256_file(image_path)}")
    else:
        parts.append("img:none")
    parts.append(f"text:{sha256_str(post_text or '')}")
    parts.append(f"url:{sha256_str(source_url or '')}")
    return sha256_str("|".join(parts))


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_atomic(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def get_media_url(m):
    if isinstance(m, str):
        return m
    if isinstance(m, dict):
        return m.get("url") or m.get("src") or m.get("mediaUrl") or m.get("fullHtml") or ""
    return ""


def collect_screenshots():
    """
    (tweetId, local_path, source_url, post_text) 수집.
    quoted.get(field) 버그 수정, 다양한 필드 지원.
    """
    media_map = load_json(DATA_DIR / "media-map.json", {})
    candidates = []

    def find_local(url):
        if not url or not url.startswith("http"):
            return None
        url_base = url.split("?")[0]
        for map_url, map_local in media_map.items():
            if url_base == map_url.split("?")[0]:
                return map_local
        for map_url, map_local in media_map.items():
            if url_base in map_url or map_url.split("?")[0] in url:
                return map_local
        return None

    for data_file in ["posts-x.json", "posts-web.json"]:
        data = load_json(DATA_DIR / data_file, {})
        posts = data.get("posts", [])
        for post in posts:
            tweet_id = str(post.get("tweetId") or post.get("id") or "")
            if not tweet_id:
                continue
            post_text = post.get("fullText") or post.get("text") or ""

            media_items = []
            for field in ["media", "mediaImages"]:
                val = post.get(field)
                if isinstance(val, list):
                    media_items.extend(val)
                elif isinstance(val, str) and val.startswith("http"):
                    media_items.append(val)

            # quoted 수정: quoted.get(field) 버그 수정
            quoted = post.get("quoted")
            if isinstance(quoted, dict):
                for field in ["media", "mediaImages"]:
                    val = quoted.get(field)  # 버그 수정: "field" → field
                    if isinstance(val, list):
                        media_items.extend(val)
                    elif isinstance(val, str) and val.startswith("http"):
                        media_items.append(val)

            seen_urls = set()
            for m in media_items:
                url = get_media_url(m)
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                local = find_local(url)
                if local:
                    full_path = V2_ROOT / local
                    if full_path.exists():
                        candidates.append((tweet_id, str(full_path), url, post_text))

            # 텍스트 내 GitHub URL (이미지 없어도)
            for owner, repo in GITHUB_RE.findall(post_text):
                candidates.append((tweet_id, None, f"https://github.com/{owner}/{repo}", post_text))

    return candidates


def github_api(path, timeout=15):
    """GitHub API 호출. 일시적 실패 시 예외."""
    url = f"https://api.github.com{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "archive-enrich-tools/3.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # 영구적: 없음
        raise  # 403/500 등은 일시적


def get_readme(owner, repo):
    """README 원문. 실패 시 None (일시적)."""
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={"Accept": "application/vnd.github.v3.raw", "User-Agent": "archive-enrich-tools/3.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ""  # README 없음 (영구적)
        raise
    return ""


def normalize_phrase(s):
    """정규화: 소문자, 특수문자 제거, 공백 단일화."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9가-힣\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_distinctive_phrases(title, min_words=2, max_phrases=5):
    """
    제목에서 distinctive phrase 추출.
    일반 단어(the, a, tool 등) 제외.
    """
    stopwords = {"the", "a", "an", "and", "or", "for", "with", "tool", "app", "new", "best"}
    words = [w for w in normalize_phrase(title).split() if w not in stopwords and len(w) > 2]
    phrases = []
    for n in [3, 2]:
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i+n])
            if phrase not in phrases:
                phrases.append(phrase)
    return phrases[:max_phrases]


def search_github_repos(query, per_page=5):
    """GitHub 저장소 검색. 일시적 실패 시 예외."""
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&per_page={per_page}"
    result = github_api(f"/search/repositories?q={urllib.parse.quote(query)}&per_page={per_page}")
    if result is None:
        return []
    return result.get("items", [])


def verify_repo_with_readme(owner, repo, expected_title="", expected_phrases=None):
    """
    저장소 검증: 실존 + 포크 아님 + README distinctive phrase 매칭.
    반환: (verified, description, is_fork, match_evidence)
    일시적 실패 시 예외 전파.
    """
    repo_clean = repo.removesuffix(".git").split("?")[0].split("#")[0]
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo_clean):
        return False, "", False, ""

    data = github_api(f"/repos/{owner}/{repo_clean}")
    if data is None:
        return False, "", False, ""  # 404

    is_fork = data.get("fork", False)
    description = data.get("description") or ""

    if is_fork:
        return False, description, True, "fork"

    # README distinctive phrase 매칭
    if expected_title or expected_phrases:
        readme = get_readme(owner, repo_clean)
        readme_norm = normalize_phrase(readme[:5000])
        desc_norm = normalize_phrase(description)

        phrases = expected_phrases or extract_distinctive_phrases(expected_title)
        matched = []
        for phrase in phrases:
            if phrase in readme_norm or phrase in desc_norm:
                matched.append(phrase)

        if not matched and phrases:
            return False, description, False, "no-phrase-match"
        return True, description, False, f"phrase:{matched[0]}" if matched else "name-match"

    return True, description, False, "no-title-check"


def search_and_verify(title, owner_hint=""):
    """
    URL 없는 GitHub-like 제목으로 검색 → README phrase 비교로 검증.
    모호한 다중 매칭은 거부.
    반환: (owner, repo, description, category_hint) 또는 (None, None, None, None)
    """
    if not title or len(title.strip()) < 4:
        return None, None, None, None

    phrases = extract_distinctive_phrases(title)
    if not phrases:
        return None, None, None, None

    # 가장 distinctive한 phrase로 검색
    query = f"{phrases[0]} {owner_hint}".strip()
    try:
        items = search_github_repos(query, per_page=5)
    except Exception:
        raise  # 일시적 실패 전파

    if not items:
        return None, None, None, None

    # 각 후보를 README phrase로 검증
    verified_candidates = []
    for item in items:
        full = item.get("full_name", "")
        if "/" not in full:
            continue
        owner, repo = full.split("/", 1)
        try:
            verified, desc, is_fork, evidence = verify_repo_with_readme(
                owner, repo, title, phrases)
        except Exception:
            raise  # 일시적 실패 전파
        if verified and not is_fork:
            verified_candidates.append((owner, repo, desc, evidence))

    # 모호한 다중 매칭 거부
    if len(verified_candidates) > 1:
        # phrase 매칭 강도로 순위, 동점이면 거부
        return None, None, None, "ambiguous"
    if len(verified_candidates) == 1:
        owner, repo, desc, evidence = verified_candidates[0]
        return owner, repo, desc, evidence
    return None, None, None, None


def korean_summary_from_readme(owner, repo, description):
    """
    README 기반 한국어 요약/분류 생성.
    Codex를 사용해 번역/분류 (캐시됨).
    """
    codex_bin = Path.home() / "workspace" / ".npm-global" / "bin" / "codex"
    if not codex_bin.exists():
        return description, "AI툴"  # 폴백

    try:
        readme = get_readme(owner, repo)[:1500]
    except Exception:
        raise

    prompt = (
        f"Repository: {owner}/{repo}\nDescription: {description}\n"
        f"README excerpt: {readme[:800]}\n\n"
        "Return ONLY JSON: {\"summary_ko\": \"1-2 sentence Korean description of what this tool does\", "
        "\"category_ko\": \"specific Korean category\"}"
    )
    try:
        result = subprocess.run(
            [str(codex_bin), "exec", "-m", "gpt-6.1-sol",
             "--sandbox", "read-only", "--skip-git-repo-check", "-"],
            input=prompt, capture_output=True, text=True, timeout=60, cwd="/tmp",
        )
        json_match = re.search(r"\{.*\}", result.stdout, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
            return data.get("summary_ko", description), data.get("category_ko", "AI툴")
    except Exception:
        pass
    return description, "AI툴"


def analyze_image_batch(image_paths, timeout=180):
    """
    배치 이미지 분석: 8개씩 Codex -i로 처리.
    반환: {image_path: analysis_dict}
    """
    codex_bin = Path.home() / "workspace" / ".npm-global" / "bin" / "codex"
    if not codex_bin.exists():
        return {}

    results = {}
    batch_size = 8
    for i in range(0, len(image_paths), batch_size):
        batch = image_paths[i:i+batch_size]
        # 인덱싱된 JSON 배열 프롬프트
        prompt_lines = [
            "Analyze each attached image in order. Return ONLY a JSON array with one object per image:",
            '[{"index": 0, "is_github": true/false, "github_urls": [...], "title": "...", '
            '"description_ko": "...", "category_ko": "...", "owner_hint": "..."}, ...]',
            "is_github: true if the screenshot shows GitHub-related content (repo page, tool with GitHub link, etc.)",
            "Even without visible URL, set is_github=true if title looks like a GitHub project.",
        ]
        args = [str(codex_bin), "exec", "-m", "gpt-6.1-sol",
                "--sandbox", "read-only", "--skip-git-repo-check"]
        for img in batch:
            args.extend(["-i", str(img)])
        args.append("-")

        try:
            result = subprocess.run(
                args, input="\n".join(prompt_lines),
                capture_output=True, text=True, timeout=timeout, cwd="/tmp",
            )
            # JSON 배열 추출
            json_match = re.search(r"\[.*\]", result.stdout, re.DOTALL)
            if json_match:
                arr = json.loads(json_match.group(0))
                for item in arr:
                    idx = item.get("index")
                    if idx is not None and 0 <= idx < len(batch):
                        results[str(batch[idx])] = item
        except Exception as e:
            log(f"배치 분석 실패: {e}")

    return results


def enrich_one(tweet_id, image_path, source_url, post_text, cache, image_memo, model_calls):
    """
    단일 처리. 반환: (entry|None, status)
    status: verified|cached|pending|pending-github-look|temporary_failure
    """
    fp = fingerprint(image_path, post_text, source_url)
    cache_key = f"{tweet_id}:{Path(image_path).name if image_path else 'text'}"

    cached = cache.get(cache_key)
    if cached and cached.get("fingerprint") == fp and cached.get("version") == CACHE_VERSION:
        return cached.get("entry"), "cached"

    # 1. 텍스트 GitHub URL 직접 검증 (한국어 요약 포함)
    text_github = GITHUB_RE.findall(post_text or "") + GITHUB_RE.findall(source_url or "")
    for owner, repo in text_github:
        try:
            verified, desc, is_fork, evidence = verify_repo_with_readme(owner, repo)
        except Exception:
            return None, "temporary_failure"
        if verified and not is_fork:
            # README 기반 한국어 요약 (캐시됨)
            try:
                summary_ko, category_ko = korean_summary_from_readme(owner, repo, desc)
            except Exception:
                return None, "temporary_failure"
            entry = {
                "title": f"{owner}/{repo}",
                "summary_ko": summary_ko,
                "category": category_ko,
                "repo_url": f"https://github.com/{owner}/{repo}",
                "status": "verified",
                "evidence": [f"text-url:{evidence}"],
                "fingerprint": fp,
            }
            cache[cache_key] = {"fingerprint": fp, "entry": entry, "version": CACHE_VERSION}
            return entry, "verified"

    # 2. 이미지 분석 (메모이제이션)
    if not image_path:
        return None, "pending"

    if fp in image_memo:
        analysis = image_memo[fp]
    else:
        # 단일 이미지 분석 (배치 호출자가 미리 채워둘 수도 있음)
        batch_result = analyze_image_batch([image_path])
        analysis = batch_result.get(str(image_path))
        if analysis is None:
            return None, "temporary_failure"
        model_calls[0] += 1
        image_memo[fp] = analysis

    if not analysis:
        return None, "temporary_failure"

    is_github = analysis.get("is_github", False)
    github_urls = analysis.get("github_urls", [])
    title = analysis.get("title", "")
    owner_hint = analysis.get("owner_hint", "")

    # 3a. GitHub URL이 보이면 검증
    for url in github_urls:
        m = GITHUB_RE.search(url)
        if not m:
            continue
        owner, repo = m.group(1), m.group(2)
        try:
            verified, desc, is_fork, evidence = verify_repo_with_readme(owner, repo, title)
        except Exception:
            return None, "temporary_failure"
        if verified and not is_fork:
            entry = {
                "title": title or f"{owner}/{repo}",
                "summary_ko": analysis.get("description_ko") or desc,
                "category": analysis.get("category_ko") or "AI툴",
                "repo_url": f"https://github.com/{owner}/{repo}",
                "status": "verified",
                "evidence": [f"screenshot:{evidence}"],
                "fingerprint": fp,
            }
            cache[cache_key] = {"fingerprint": fp, "entry": entry, "version": CACHE_VERSION}
            return entry, "verified"

    # 3b. URL 없지만 GitHub-like 제목이면 검색
    if is_github and title:
        try:
            owner, repo, desc, evidence = search_and_verify(title, owner_hint)
        except Exception:
            return None, "temporary_failure"
        if evidence == "ambiguous":
            # 모호함 → negative로 캐시하지 않음
            return None, "pending-github-look"
        if owner and repo:
            entry = {
                "title": title,
                "summary_ko": analysis.get("description_ko") or desc,
                "category": analysis.get("category_ko") or "AI툴",
                "repo_url": f"https://github.com/{owner}/{repo}",
                "status": "verified",
                "evidence": [f"search:{evidence}"],
                "fingerprint": fp,
            }
            cache[cache_key] = {"fingerprint": fp, "entry": entry, "version": CACHE_VERSION}
            return entry, "verified"
        # GitHub-like이지만 못 찾음 → negative로 캐시하지 않음
        return None, "pending-github-look"

    # 4. 진짜 negative (분석 성공, GitHub 아님)
    cache[cache_key] = {"fingerprint": fp, "entry": None, "version": CACHE_VERSION}
    return None, "pending"


def update_manifest():
    """manifest.ai_tools 업데이트."""
    manifest = load_json(MANIFEST_JSON, {})
    if not TOOLS_JSON.exists():
        return
    sha = sha256_file(str(TOOLS_JSON))
    tools = load_json(TOOLS_JSON, {})
    manifest["ai_tools"] = {
        "file": "data/ai-tools.json",
        "sha256": sha,
        "count": len(tools),
    }
    save_json_atomic(MANIFEST_JSON, manifest)
    log(f"manifest.ai_tools 업데이트: count={len(tools)}")


def run_backfill(max_items=None, dry_run=False, batch_size=8):
    """
    전체 백필 (배치 단위 원자적 저장).
    220개 × 20초 > 900초 훅 타임아웃 → 배치 저장 필수.
    """
    tools_data = load_json(TOOLS_JSON, {})
    cache = load_json(CACHE_JSON, {})
    progress = load_json(PROGRESS_JSON, {"completed": [], "pending_details": {}})
    image_memo = {}
    model_calls = [0]

    candidates = collect_screenshots()
    log(f"후보: {len(candidates)}개")

    # 이미 완료된 것은 건너뜀 (progress 기반 resume)
    completed_keys = set(progress.get("completed", []))
    remaining = []
    for c in candidates:
        key = f"{c[0]}:{Path(c[1]).name if c[1] else 'text'}"
        if key not in completed_keys:
            remaining.append(c)

    log(f"남은 작업: {len(remaining)}개 (완료: {len(completed_keys)})")

    stats = {"verified": 0, "pending": 0, "pending_github_look": 0,
             "cached": 0, "temporary_failures": 0, "errors": 0}
    pending_details = progress.get("pending_details", {})

    items = remaining[:max_items] if max_items else remaining

    # 배치 단위 처리 (이미지 배치 분석 + 원자적 저장)
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start:batch_start + batch_size]

        # 배치 내 유니크 이미지 미리 분석
        unique_images = list({c[1] for c in batch if c[1]})
        if unique_images:
            batch_analyses = analyze_image_batch(unique_images)
            for img_path, analysis in batch_analyses.items():
                # 지문으로 메모이제이션
                for c in batch:
                    if c[1] == img_path:
                        fp = fingerprint(c[1], c[3], c[2])
                        image_memo[fp] = analysis
            model_calls[0] += len(batch_analyses)

        for tweet_id, image_path, source_url, post_text in batch:
            key = f"{tweet_id}:{Path(image_path).name if image_path else 'text'}"
            try:
                entry, status = enrich_one(
                    tweet_id, image_path, source_url, post_text, cache, image_memo, model_calls)
                if status == "cached":
                    stats["cached"] += 1
                    # 캐시 히트도 tools_data에 재적용
                    if entry and not dry_run:
                        tools_data[str(tweet_id)] = entry
                elif status == "verified" and entry:
                    if not dry_run:
                        tools_data[str(tweet_id)] = entry
                    stats["verified"] += 1
                    log(f"  ✓ {tweet_id}: {entry['repo_url']}")
                elif status == "temporary_failure":
                    stats["temporary_failures"] += 1
                elif status == "pending-github-look":
                    stats["pending_github_look"] += 1
                    pending_details[tweet_id] = "GitHub-like title but no verified repo (not cached as negative)"
                else:
                    stats["pending"] += 1
                    # pending 사유 기록
                    if tweet_id not in pending_details:
                        pending_details[tweet_id] = "No GitHub content detected"
            except Exception as e:
                stats["errors"] += 1
                log(f"  ✗ {tweet_id}: {e}")

            completed_keys.add(key)

        # 배치 단위 원자적 저장
        if not dry_run:
            save_json_atomic(TOOLS_JSON, tools_data)
            save_json_atomic(CACHE_JSON, cache)
            progress["completed"] = list(completed_keys)
            progress["pending_details"] = pending_details
            save_json_atomic(PROGRESS_JSON, progress)
            update_manifest()
            log(f"  배치 저장 완료 ({batch_start + len(batch)}/{len(items)})")

    # stale 검증 제거: 지문이 변경된 항목은 tools_data에서 제거
    # (fingerprint가 현재와 다르면 stale)
    if not dry_run:
        current_fps = {}
        for c in candidates:
            key = f"{c[0]}:{Path(c[1]).name if c[1] else 'text'}"
            current_fps[c[0]] = fingerprint(c[1], c[3], c[2])
        stale = []
        for tid, entry in list(tools_data.items()):
            if isinstance(entry, dict) and entry.get("fingerprint"):
                if entry["fingerprint"] != current_fps.get(tid):
                    stale.append(tid)
        for tid in stale:
            del tools_data[tid]
            log(f"  stale 제거: {tid}")
        if stale:
            save_json_atomic(TOOLS_JSON, tools_data)
            update_manifest()

    # 재시도 마커 (dry_run이 아닐 때만)
    if not dry_run:
        if stats["temporary_failures"] > 0 or stats["errors"] > 0:
            RETRY_MARKER.parent.mkdir(parents=True, exist_ok=True)
            RETRY_MARKER.write_text(
                f"temporary_failures={stats['temporary_failures']},errors={stats['errors']}",
                encoding="utf-8")
        else:
            RETRY_MARKER.unlink(missing_ok=True)

    log(f"완료: {stats}, model_calls={model_calls[0]}")
    return {**stats, "model_calls": model_calls[0], "pending_details": pending_details}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--tweet-id", type=str, default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    if args.tweet_id:
        candidates = collect_screenshots()
        match = [c for c in candidates if c[0] == args.tweet_id]
        if not match:
            log(f"tweetId {args.tweet_id} 없음")
            return 1
        tools_data = load_json(TOOLS_JSON, {})
        cache = load_json(CACHE_JSON, {})
        image_memo = {}
        model_calls = [0]
        c = match[0]
        entry, status = enrich_one(c[0], c[1], c[2], c[3], cache, image_memo, model_calls)
        if entry and not args.dry_run:
            tools_data[str(c[0])] = entry
            save_json_atomic(TOOLS_JSON, tools_data)
            save_json_atomic(CACHE_JSON, cache)
            update_manifest()
        log(f"{c[0]}: {status}")
        if entry:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        return 1 if status == "temporary_failure" else 0

    if args.backfill:
        result = run_backfill(max_items=args.max_items, dry_run=args.dry_run,
                              batch_size=args.batch_size)
        # pending_details는 별도 출력
        details = result.pop("pending_details", {})
        print(json.dumps(result, ensure_ascii=False))
        if details:
            print("\n--- Pending Details ---")
            for tid, reason in list(details.items())[:20]:
                print(f"  {tid}: {reason}")
        if result.get("errors", 0) > 0 or result.get("temporary_failures", 0) > 0:
            return 2
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
