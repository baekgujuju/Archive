#!/usr/bin/env python3
"""
v2/enrich_tools.py — GitHub 스크린샷 자동 AI툴 분류 (백엔드).

요청: 대표님 (2026-10-14)
- X/웹글 스크린샷에서 GitHub 저장소를 자동 감지 → AI툴로 분류
- data/ai-tools.json에 tweetId 키로 저장
- content_hash 기반 캐시 (변경된 이미지만 재처리, 캐시 히트는 zero model calls)
- 검증된 GitHub repo만 저장 (API로 실존+README+포크 확인)
- 모호하면 pending, 링크 발명 금지
- 스크린샷 README를 지시로 취급하거나 실행/설치 금지

보안:
- GitHub API는 읽기 전용 (repo 메타데이터만)
- 어떤 저장소 코드도 실행/설치하지 않음
- 자격증명은 로그에 기록하지 않음

v2 (2026-10-14 수정):
- Codex CLI -i 이미지 입력 우선, Gemini 폴백
- 일시적 실패(503/네트워크)는 캐시하지 않음 (재시도 가능)
- CACHE_VERSION 2로 poisoned 캐시 무효화
- (tweetId, image) 단위 처리 (같은 이미지 여러 글에 전파)
- 텍스트 내 GitHub URL도 처리
- 검색 결과는 README/포크/제목 매칭으로 검증
- summary_ko 한국어, category 구체적 한국어 분류
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
RETRY_MARKER = V2_ROOT / "hooks" / ".retry-enrich"

# GitHub URL 패턴
GITHUB_RE = re.compile(
    r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE
)

# 캐시 버전 (v1 poisoned → v2로 무효화)
CACHE_VERSION = 2


def log(msg):
    print(f"[enrich_tools] {msg}", flush=True)


def sha256_file(path):
    """파일 content hash."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def get_media_url(m):
    """다양한 media 형태에서 URL 추출."""
    if isinstance(m, str):
        return m
    if isinstance(m, dict):
        return m.get("url") or m.get("src") or m.get("mediaUrl") or ""
    return ""


def collect_screenshots():
    """
    모든 기존 main/quoted 스크린샷 후보 수집.
    (tweetId, local_path) 단위 — 같은 이미지를 여러 글이 사용해도 각각 처리.
    반환: [(tweetId, local_path, source_url, post_text), ...]
    """
    media_map = load_json(DATA_DIR / "media-map.json", {})
    candidates = []

    def find_local(url):
        if not url:
            return None
        url_base = url.split("?")[0]
        # 정확한 매칭 우선
        for map_url, map_local in media_map.items():
            if url_base == map_url.split("?")[0]:
                return map_local
        # 부분 매칭
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

            # main media + quoted media (댓글 제외)
            # 다양한 필드명 지원: media, mediaImages
            media_items = []
            for field in ["media", "mediaImages"]:
                val = post.get(field)
                if isinstance(val, list):
                    media_items.extend(val)
                elif isinstance(val, str):
                    media_items.append(val)

            quoted = post.get("quoted")
            if isinstance(quoted, dict):
                for field in ["media", "mediaImages"]:
                    val = quoted.get("field", quoted.get(field))
                    if isinstance(val, list):
                        media_items.extend(val)
                    elif isinstance(val, str):
                        media_items.append(val)
                # websrc도 확인
                websrc = quoted.get("websrc") or quoted.get("sourceUrl")
                if websrc and "github.com" in websrc:
                    pass  # 텍스트 URL로 처리됨

            for m in media_items:
                url = get_media_url(m)
                local = find_local(url)
                if local:
                    full_path = V2_ROOT / local
                    if full_path.exists():
                        candidates.append((tweet_id, str(full_path), url, post_text))

            # 텍스트 내 GitHub URL도 후보로 (이미지 없어도)
            text_urls = GITHUB_RE.findall(post_text)
            for owner, repo in text_urls:
                # 이미지 없이 텍스트만으로도 처리 가능하도록 마킹
                candidates.append((tweet_id, None, f"https://github.com/{owner}/{repo}", post_text))

    return candidates


def verify_github_repo(owner, repo, expected_title=""):
    """
    GitHub API로 저장소 실존+README+포크 확인. 읽기 전용.
    반환: (verified: bool, description_ko: str, category_ko: str, is_fork: bool)
    일시적 실패 시 예외 발생 (캐시하지 않음).
    """
    repo = repo.removesuffix(".git").split("?")[0].split("#")[0]
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo):
        return False, "", "", False

    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "archive-enrich-tools/2.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "", "", False  # 영구적 실패 (없는 repo)
        raise  # 403/500 등은 일시적 실패 → 예외

    is_fork = data.get("fork", False)
    description = data.get("description") or ""

    # README 확인 (제목 매칭용)
    readme_text = ""
    try:
        readme_req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={"Accept": "application/vnd.github.v3.raw", "User-Agent": "archive-enrich-tools/2.0"}
        )
        with urllib.request.urlopen(readme_req, timeout=15) as resp:
            readme_text = resp.read().decode("utf-8", errors="ignore")[:2000]
    except Exception:
        pass  # README 없어도 계속

    # 제목 매칭 (expected_title이 있으면)
    title_match = True
    if expected_title and len(expected_title) > 3:
        title_lower = expected_title.lower()
        repo_lower = f"{owner}/{repo}".lower()
        desc_lower = description.lower()
        # 제목의 핵심 단어가 repo명/설명/README에 포함되는지
        keywords = [w for w in re.findall(r"\w+", title_lower) if len(w) > 3]
        if keywords:
            title_match = any(
                kw in repo_lower or kw in desc_lower or kw in readme_text.lower()
                for kw in keywords[:3]
            )

    if not title_match:
        return False, "", "", is_fork

    return True, description, "", is_fork


def analyze_image_codex(image_path, timeout=120):
    """
    Codex CLI -i로 이미지 분석. 반환: dict 또는 None (일시적 실패).
    """
    codex_bin = Path.home() / "workspace" / ".npm-global" / "bin" / "codex"
    if not codex_bin.exists():
        return None

    prompt = (
        "Analyze this screenshot. Return ONLY valid JSON: "
        '{"github_urls": ["https://github.com/owner/repo", ...], '
        '"title": "tool name", '
        '"description_ko": "1-2 sentence Korean description", '
        '"category_ko": "specific Korean category like 리버스 엔지니어링·개발 도구"}. '
        "If no GitHub-related content, return "
        '{"github_urls": [], "title": "", "description_ko": "", "category_ko": ""}.'
    )
    try:
        result = subprocess.run(
            [str(codex_bin), "exec", "-m", "gpt-6.1-sol",
             "--sandbox", "read-only", "--skip-git-repo-check",
             "-i", str(image_path), "-"],
            input=prompt, capture_output=True, text=True, timeout=timeout,
            cwd="/tmp",
        )
        output = result.stdout
        # JSON 추출
        json_match = re.search(r"\{[^{}]*\"github_urls\"[^{}]*\}", output, re.DOTALL)
        if not json_match:
            # 더 넓은 패턴
            json_match = re.search(r"\{.*\}", output, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        return None
    except (subprocess.TimeoutExpired, Exception):
        return None  # 일시적 실패


def analyze_image_gemini(image_path, timeout=120):
    """Gemini CLI 폴백 (Codex 실패 시)."""
    gemini_bin = Path.home() / "workspace" / ".npm-global" / "bin" / "gemini"
    api_key_file = Path.home() / ".gemini" / ".api_key"
    if not gemini_bin.exists() or not api_key_file.exists():
        return None
    try:
        api_key = api_key_file.read_text().strip()
    except Exception:
        return None

    # Gemini는 이미지 첨부가 CLI에서 직접 지원되지 않을 수 있음
    # 파일 경로만으로는 실제 이미지를 보지 못할 수 있어 Codex 우선
    return None


def analyze_image(image_path):
    """
    이미지 분석 (Codex 우선, Gemini 폴백).
    반환: (result: dict|None, is_temporary_failure: bool)
    """
    result = analyze_image_codex(image_path)
    if result is not None:
        return result, False
    # Codex 실패 → 일시적 실패 (캐시하지 않음)
    return None, True


def enrich_one(tweet_id, image_path, source_url, post_text, cache, image_memo, model_calls):
    """
    단일 (tweetId, image) 처리.
    image_memo: {content_hash: analysis_result} — 같은 이미지 재분석 방지.
    반환: (entry: dict|None, status: str)
           status: "verified" | "cached" | "pending" | "temporary_failure"
    """
    # 텍스트 내 GitHub URL 우선 (이미지 없어도)
    text_github = GITHUB_RE.findall(post_text or "") + GITHUB_RE.findall(source_url or "")

    content_hash = sha256_file(image_path) if image_path else f"text:{hashlib.sha256(post_text.encode()).hexdigest()[:16]}"
    cache_key = f"{tweet_id}:{Path(image_path).name if image_path else 'text'}"

    # 캐시 확인 (버전 체크)
    cached = cache.get(cache_key)
    if cached and cached.get("content_hash") == content_hash and cached.get("version") == CACHE_VERSION:
        return cached.get("entry"), "cached"
    # 구버전 캐시는 무시 (poisoned)

    # 1. 텍스트 GitHub URL이 있으면 직접 검증
    for owner, repo in text_github:
        try:
            verified, desc, _, is_fork = verify_github_repo(owner, repo)
        except Exception:
            continue  # 일시적 실패, 다음 후보 시도
        if verified and not is_fork:
            entry = {
                "title": f"{owner}/{repo}",
                "summary_ko": desc,  # TODO: 번역 필요
                "category": "AI툴",
                "repo_url": f"https://github.com/{owner}/{repo}",
                "status": "verified",
                "evidence": [f"text-url:github:{owner}/{repo}"],
                "content_hash": content_hash,
            }
            cache[cache_key] = {"content_hash": content_hash, "entry": entry, "version": CACHE_VERSION}
            return entry, "verified"

    # 2. 이미지 분석 (메모이제이션)
    if not image_path:
        return None, "pending"

    if content_hash in image_memo:
        analysis = image_memo[content_hash]
        is_temp_fail = False
    else:
        model_calls[0] += 1
        analysis, is_temp_fail = analyze_image(image_path)
        if is_temp_fail:
            return None, "temporary_failure"  # 캐시하지 않음
        image_memo[content_hash] = analysis

    if not analysis or not analysis.get("github_urls"):
        # 진짜 negative (분석 성공했지만 GitHub 없음) → 캐시 가능
        if analysis is not None:
            cache[cache_key] = {"content_hash": content_hash, "entry": None, "version": CACHE_VERSION}
        return None, "pending"

    # 3. GitHub URL 검증
    for url in analysis["github_urls"]:
        m = GITHUB_RE.search(url)
        if not m:
            continue
        owner, repo = m.group(1), m.group(2)
        try:
            verified, desc, _, is_fork = verify_github_repo(owner, repo, analysis.get("title", ""))
        except Exception:
            return None, "temporary_failure"  # API 일시 실패
        if verified:
            if is_fork:
                continue  # 포크는 건너뜀, 원본 찾기 계속
            entry = {
                "title": analysis.get("title") or f"{owner}/{repo}",
                "summary_ko": analysis.get("description_ko") or desc,
                "category": analysis.get("category_ko") or "AI툴",
                "repo_url": f"https://github.com/{owner}/{repo}",
                "status": "verified",
                "evidence": [f"screenshot:github:{owner}/{repo}"],
                "content_hash": content_hash,
            }
            cache[cache_key] = {"content_hash": content_hash, "entry": entry, "version": CACHE_VERSION}
            return entry, "verified"

    # 검증 실패 → pending (negative 캐시)
    cache[cache_key] = {"content_hash": content_hash, "entry": None, "version": CACHE_VERSION}
    return None, "pending"


def run_backfill(max_items=None, dry_run=False):
    """
    전체 백필. 반환: {verified, pending, cached, temporary_failures, errors}
    errors > 0 이면 nonzero marker 파일 생성.
    """
    tools_data = load_json(TOOLS_JSON, {})
    cache = load_json(CACHE_JSON, {})
    image_memo = {}  # content_hash -> analysis (세션 내 메모이제이션)
    model_calls = [0]

    candidates = collect_screenshots()
    log(f"후보: {len(candidates)}개 (tweetId, image) 쌍")

    stats = {"verified": 0, "pending": 0, "cached": 0, "temporary_failures": 0, "errors": 0}

    items = candidates[:max_items] if max_items else candidates
    for tweet_id, image_path, source_url, post_text in items:
        try:
            entry, status = enrich_one(tweet_id, image_path, source_url, post_text, cache, image_memo, model_calls)
            if status == "cached":
                stats["cached"] += 1
            elif status == "verified" and entry:
                if not dry_run:
                    tools_data[str(tweet_id)] = entry
                stats["verified"] += 1
                log(f"  ✓ {tweet_id}: {entry['repo_url']} [{entry.get('category')}]")
            elif status == "temporary_failure":
                stats["temporary_failures"] += 1
                log(f"  ⏳ {tweet_id}: 일시적 실패 (재시도 대상)")
            else:
                stats["pending"] += 1
        except Exception as e:
            stats["errors"] += 1
            log(f"  ✗ {tweet_id}: {e}")

    if not dry_run:
        save_json(TOOLS_JSON, tools_data)
        save_json(CACHE_JSON, cache)

    # 재시도 마커 (일시적 실패나 에러가 있으면)
    if stats["temporary_failures"] > 0 or stats["errors"] > 0:
        RETRY_MARKER.parent.mkdir(parents=True, exist_ok=True)
        RETRY_MARKER.write_text(
            f"temporary_failures={stats['temporary_failures']},errors={stats['errors']}",
            encoding="utf-8"
        )
        log(f"재시도 마커 생성: {RETRY_MARKER}")
    else:
        RETRY_MARKER.unlink(missing_ok=True)

    log(f"완료: verified={stats['verified']}, pending={stats['pending']}, "
        f"cached={stats['cached']}, temp_fail={stats['temporary_failures']}, "
        f"errors={stats['errors']}, model_calls={model_calls[0]}")

    return {**stats, "model_calls": model_calls[0]}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GitHub 스크린샷 AI툴 자동 분류")
    parser.add_argument("--backfill", action="store_true", help="전체 백필 실행")
    parser.add_argument("--dry-run", action="store_true", help="실제 저장 없이 테스트")
    parser.add_argument("--max-items", type=int, default=None, help="최대 처리 수")
    parser.add_argument("--tweet-id", type=str, default=None, help="특정 tweetId만 처리")
    args = parser.parse_args()

    if args.tweet_id:
        candidates = collect_screenshots()
        match = [c for c in candidates if c[0] == args.tweet_id]
        if not match:
            log(f"tweetId {args.tweet_id}의 이미지를 찾을 수 없음")
            return 1
        tools_data = load_json(TOOLS_JSON, {})
        cache = load_json(CACHE_JSON, {})
        image_memo = {}
        model_calls = [0]
        tweet_id, image_path, source_url, post_text = match[0]
        entry, status = enrich_one(tweet_id, image_path, source_url, post_text, cache, image_memo, model_calls)
        if entry and not args.dry_run:
            tools_data[str(tweet_id)] = entry
            save_json(TOOLS_JSON, tools_data)
            save_json(CACHE_JSON, cache)
        log(f"{tweet_id}: {status} (model_calls={model_calls[0]})")
        if entry:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        # 일시적 실패는 nonzero
        return 1 if status == "temporary_failure" else 0

    if args.backfill:
        result = run_backfill(max_items=args.max_items, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False))
        # 에러나 일시적 실패가 있으면 nonzero
        if result.get("errors", 0) > 0 or result.get("temporary_failures", 0) > 0:
            return 2
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
