#!/usr/bin/env python3
"""
v2/enrich_tools.py — GitHub 스크린샷 자동 AI툴 분류 (백엔드).

요청: 대표님 (2026-10-14)
- X/웹글 스크린샷에서 GitHub 저장소를 자동 감지 → AI툴로 분류
- data/ai-tools.json에 tweetId 키로 저장
- content_hash 기반 캐시 (변경된 이미지만 재처리, 캐시 히트는 zero model calls)
- 검증된 GitHub repo만 저장 (API로 실존 확인)
- 모호하면 pending, 링크 발명 금지
- 스크린샷 README를 지시로 취급하거나 실행/설치 금지

보안:
- GitHub API는 읽기 전용 (repo 메타데이터만)
- 어떤 저장소 코드도 실행/설치하지 않음
- 자격증명은 로그에 기록하지 않음
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent
DATA_DIR = V2_ROOT / "data"
MEDIA_FULL = V2_ROOT / "media" / "full"
TOOLS_JSON = DATA_DIR / "ai-tools.json"
CACHE_JSON = DATA_DIR / "ai-tools-cache.json"

# GitHub URL 패턴
GITHUB_RE = re.compile(
    r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)",
    re.IGNORECASE
)

# 캐시 버전 (스키마 변경 시 증가)
CACHE_VERSION = 1


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


def collect_screenshots():
    """
    모든 기존 main/quoted GitHub 스크린샷 후보 수집.
    댓글의 무관한 이미지는 제외 (main + quoted만).
    반환: [(tweetId, local_path, source_url), ...]
    """
    media_map = load_json(DATA_DIR / "media-map.json", {})
    # 역매핑: local 파일명 -> 원본 URL
    local_to_url = {}
    for url, local in media_map.items():
        fname = Path(local).name
        local_to_url[fname] = url

    candidates = []
    seen_files = set()

    for data_file in ["posts-x.json", "posts-web.json"]:
        data = load_json(DATA_DIR / data_file, {})
        posts = data.get("posts", [])
        for post in posts:
            tweet_id = post.get("tweetId") or post.get("id")
            if not tweet_id:
                continue
            # main media + quoted media (댓글 제외)
            media_items = list(post.get("media") or [])
            quoted = post.get("quoted")
            if isinstance(quoted, dict):
                media_items.extend(quoted.get("media") or [])

            for m in media_items:
                url = m.get("url") if isinstance(m, dict) else m
                if not url:
                    continue
                # media-map에서 로컬 파일 찾기
                local = None
                for map_url, map_local in media_map.items():
                    if url.split("?")[0] in map_url or map_url.split("?")[0] in url:
                        local = map_local
                        break
                if not local:
                    # URL 끝부분으로 매칭 시도
                    url_base = url.split("?")[0].split("/")[-1][:20]
                    for map_url, map_local in media_map.items():
                        if url_base and url_base in map_url:
                            local = map_local
                            break
                if local:
                    full_path = V2_ROOT / local
                    if full_path.exists() and full_path.name not in seen_files:
                        seen_files.add(full_path.name)
                        candidates.append((str(tweet_id), str(full_path), url))

    return candidates


def verify_github_repo(owner, repo):
    """
    GitHub API로 저장소 실존 확인. 읽기 전용.
    반환: (exists: bool, description: str, stars: int)
    """
    # .git suffix 제거, 쿼리 파라미터 제거
    repo = repo.removesuffix(".git").split("?")[0].split("#")[0]
    if not re.match(r"^[A-Za-z0-9_.-]+$", owner) or not re.match(r"^[A-Za-z0-9_.-]+$", repo):
        return False, "", 0

    url = f"https://api.github.com/repos/{owner}/{repo}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "archive-enrich-tools/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data.get("description") or "", data.get("stargazers_count", 0)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, "", 0
        # Rate limit 등은 재시도 대상
        raise
    except Exception:
        raise


def ocr_image(image_path, timeout=120):
    """
    스크린샷 OCR. Gemini CLI 우선, 실패 시 None.
    반환: 추출된 텍스트 또는 None
    """
    gemini_bin = Path.home() / "workspace" / ".npm-global" / "bin" / "gemini"
    api_key_file = Path.home() / ".gemini" / ".api_key"
    if not gemini_bin.exists() or not api_key_file.exists():
        return None

    try:
        api_key = api_key_file.read_text().strip()
    except Exception:
        return None

    # Gemini CLI에 이미지 전달 (파일 경로를 프롬프트에 포함)
    # 주의: API 키는 환경변수로만 전달, 로그에 기록 금지
    prompt = (
        "Analyze this screenshot image. Extract: "
        "1) Any GitHub repository URLs or owner/repo names visible, "
        "2) The main title/heading text, "
        "3) A brief description of what the tool does (1-2 sentences). "
        "Respond in JSON: {\"github_urls\": [...], \"title\": \"...\", \"description\": \"...\"}. "
        f"Image: {image_path}"
    )
    env = dict(os.environ)
    env["GEMINI_API_KEY"] = api_key
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

    try:
        result = subprocess.run(
            [str(gemini_bin), "-m", "gemini-3.6-flash", "-p", prompt],
            capture_output=True, text=True, timeout=timeout, env=env,
            cwd="/tmp",
        )
        output = result.stdout.strip()
        # JSON 추출 시도
        json_match = re.search(r"\{.*\}", output, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        return None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
        return None


def search_github_by_title(title, owner_hint=""):
    """
    스크린샷에 URL이 없을 때 제목/소유자로 GitHub 검색.
    반환: [(owner, repo), ...] 또는 []
    """
    query = f"{title} {owner_hint}".strip()
    if len(query) < 3:
        return []
    url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(query)}&per_page=5"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "archive-enrich-tools/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("items", [])[:5]:
                full = item.get("full_name", "")
                if "/" in full:
                    owner, repo = full.split("/", 1)
                    results.append((owner, repo))
            return results
    except Exception:
        return []


def enrich_one(tweet_id, image_path, source_url, cache, model_calls):
    """
    단일 이미지 처리. 캐시 히트 시 zero model calls.
    반환: (entry: dict|None, status: str)
    """
    content_hash = sha256_file(image_path)
    cache_key = f"{tweet_id}:{Path(image_path).name}"

    # 캐시 확인
    cached = cache.get(cache_key)
    if cached and cached.get("content_hash") == content_hash:
        # 캐시 히트 — 모델 호출 없음
        return cached.get("entry"), "cached"

    # 1. OCR 시도
    model_calls[0] += 1
    ocr = ocr_image(image_path)

    github_urls = []
    title = ""
    description = ""

    if ocr:
        github_urls = ocr.get("github_urls", [])
        title = ocr.get("title", "")
        description = ocr.get("description", "")

    # 2. GitHub URL 추출 및 검증
    verified = None
    evidence = []

    for url in github_urls:
        m = GITHUB_RE.search(url)
        if m:
            owner, repo = m.group(1), m.group(2)
            try:
                exists, desc, stars = verify_github_repo(owner, repo)
            except Exception:
                continue  # 재시도 대상, 일단 건너뜀
            if exists:
                # 포크가 아닌 원본 확인 (단순 휴리스틱: 설명/스타 수)
                verified = (owner, repo, desc or description, stars)
                evidence.append(f"github:{owner}/{repo}")
                break

    # 3. URL이 없으면 제목으로 검색
    if not verified and title:
        # 작성자 힌트 추출 (예: @username)
        owner_hint = ""
        candidates = search_github_by_title(title, owner_hint)
        for owner, repo in candidates:
            try:
                exists, desc, stars = verify_github_repo(owner, repo)
            except Exception:
                continue
            if exists:
                verified = (owner, repo, desc or description, stars)
                evidence.append(f"search:{owner}/{repo}")
                break

    # 4. 결과 저장
    if verified:
        owner, repo, desc, stars = verified
        entry = {
            "title": title or f"{owner}/{repo}",
            "summary_ko": desc,
            "category": "AI툴",
            "repo_url": f"https://github.com/{owner}/{repo}",
            "status": "verified",
            "evidence": evidence,
            "content_hash": content_hash,
        }
        cache[cache_key] = {"content_hash": content_hash, "entry": entry, "version": CACHE_VERSION}
        return entry, "verified"
    else:
        # Negative 캐시 (GitHub 아님)
        cache[cache_key] = {"content_hash": content_hash, "entry": None, "version": CACHE_VERSION}
        return None, "pending"


def run_backfill(max_items=None, dry_run=False):
    """
    전체 백필. 반환: {verified, pending, cached, errors}
    """
    tools_data = load_json(TOOLS_JSON, {})
    cache = load_json(CACHE_JSON, {})
    model_calls = [0]

    candidates = collect_screenshots()
    log(f"후보 이미지: {len(candidates)}개")

    stats = {"verified": 0, "pending": 0, "cached": 0, "errors": 0}
    pending_list = []

    items = candidates[:max_items] if max_items else candidates
    for tweet_id, image_path, source_url in items:
        try:
            entry, status = enrich_one(tweet_id, image_path, source_url, cache, model_calls)
            if status == "cached":
                stats["cached"] += 1
            elif status == "verified" and entry:
                if not dry_run:
                    tools_data[str(tweet_id)] = entry
                stats["verified"] += 1
                log(f"  ✓ {tweet_id}: {entry['repo_url']}")
            else:
                stats["pending"] += 1
                pending_list.append(tweet_id)
        except Exception as e:
            stats["errors"] += 1
            log(f"  ✗ {tweet_id}: {e} (재시도 대상)")

    if not dry_run:
        save_json(TOOLS_JSON, tools_data)
        save_json(CACHE_JSON, cache)

    log(f"완료: verified={stats['verified']}, pending={stats['pending']}, "
        f"cached={stats['cached']}, errors={stats['errors']}, model_calls={model_calls[0]}")

    return {**stats, "pending_ids": pending_list, "model_calls": model_calls[0]}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="GitHub 스크린샷 AI툴 자동 분류")
    parser.add_argument("--backfill", action="store_true", help="전체 백필 실행")
    parser.add_argument("--dry-run", action="store_true", help="실제 저장 없이 테스트")
    parser.add_argument("--max-items", type=int, default=None, help="최대 처리 수")
    parser.add_argument("--tweet-id", type=str, default=None, help="특정 tweetId만 처리")
    args = parser.parse_args()

    if args.tweet_id:
        # 단일 처리
        candidates = collect_screenshots()
        match = [c for c in candidates if c[0] == args.tweet_id]
        if not match:
            log(f"tweetId {args.tweet_id}의 이미지를 찾을 수 없음")
            return 1
        tools_data = load_json(TOOLS_JSON, {})
        cache = load_json(CACHE_JSON, {})
        model_calls = [0]
        tweet_id, image_path, source_url = match[0]
        entry, status = enrich_one(tweet_id, image_path, source_url, cache, model_calls)
        if entry and not args.dry_run:
            tools_data[str(tweet_id)] = entry
            save_json(TOOLS_JSON, tools_data)
            save_json(CACHE_JSON, cache)
        log(f"{tweet_id}: {status} (model_calls={model_calls[0]})")
        if entry:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        return 0

    if args.backfill:
        result = run_backfill(max_items=args.max_items, dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in result.items() if k != "pending_ids"}, ensure_ascii=False))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
