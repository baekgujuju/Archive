#!/usr/bin/env python3
"""
Naver/웹 원문 수집 스크립트.
- private-sources/ 에 저장 (공개 GitHub에 커밋하지 않음)
- targets.json 기반, resume 가능
- 2026-10-14 Codex 감사 지적 수정 반영

주의: 네이버 도메인은 인증된 개인 브라우저로만 수집.
fetch.sh는 비인증 프로필이므로 네이버 URL에 사용 금지.
"""
import json
import subprocess
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parent
PRIVATE_DIR = V2_ROOT / "data" / "private-sources"
TARGETS_JSON = PRIVATE_DIR / "targets.json"
RESULTS_JSON = PRIVATE_DIR / "collection-results.json"
FETCH_SH = Path.home() / "workspace" / "skills" / "web-scrape" / "scripts" / "fetch.sh"

# source_quality 게이트 (선택적)
try:
    sys.path.insert(0, str(V2_ROOT))
    from source_quality import assess_capture
    HAS_QUALITY_GATE = True
except ImportError:
    HAS_QUALITY_GATE = False


def log(msg):
    """수집 로그 출력."""
    print(f"[collect] {msg}", flush=True)


def is_naver_url(url):
    """네이버 도메인 여부."""
    return "naver.com" in url or "naver.me" in url


def is_navigation_content(text):
    """
    네비게이션 오인 방지 (2026-10-14 대표님 지시).
    카페 정보·멤버수·게시판 목록이 주 내용이면 True.
    """
    nav_markers = ["카페정보", "카페멤버수", "게시판", "카페등급"]
    nav_count = sum(1 for m in nav_markers if m in text[:2000])
    return nav_count >= 3


def validate_article_body(text, expected_title=""):
    """
    실제 글 본문인지 검증.
    - 네비게이션이면 False
    - 본문이 500자 미만이면 False
    - expected_title이 있으면 제목 포함 여부 확인
    """
    if not text or len(text.strip()) < 500:
        return False
    if is_navigation_content(text):
        return False
    if expected_title and len(expected_title) > 5:
        # 제목의 핵심 단어가 본문에 있는지 확인
        title_words = [w for w in expected_title.split() if len(w) > 1][:3]
        if title_words and not any(w in text[:2000] for w in title_words):
            return False
    return True


def main():
    targets = json.load(open(TARGETS_JSON))
    results = {}
    if RESULTS_JSON.exists():
        results = json.load(open(RESULTS_JSON))

    pending = [t for t in targets if t.get("id") and t["id"] not in results]
    log(f"전체 {len(targets)}, 완료 {len(results)}, 남은 {len(pending)}")

    for t in pending:
        tid = t["id"]
        url = t.get("url", "")
        title = t.get("title", "")
        if not url:
            results[tid] = {"status": "no-url", "reason": "URL 없음"}
            continue

        # 네이버 URL은 인증 브라우저로만 수집 (fetch.sh 사용 금지)
        if is_naver_url(url):
            results[tid] = {
                "status": "needs-auth-browser",
                "reason": "네이버 인증 브라우저 수집 필요",
                "url": url,
                "title": title,
            }
            log(f"  {tid}: 네이버 URL, 인증 브라우저 대기")
            json.dump(results, open(RESULTS_JSON, "w"), ensure_ascii=False, indent=2)
            continue

        out_dir = PRIVATE_DIR / tid
        out_dir.mkdir(parents=True, exist_ok=True)
        out_md = out_dir / "article.md"

        log(f"수집 중: {tid} ({title[:30]})")
        try:
            r = subprocess.run(
                ["bash", str(FETCH_SH), url, "--output", str(out_md), "--download-media"],
                capture_output=True, text=True, timeout=180
            )
            if out_md.exists():
                size = out_md.stat().st_size
                text = out_md.read_text(encoding="utf-8", errors="ignore")
                # 본문 길이 측정 (frontmatter 제외)
                body = text.split("---", 2)[-1] if text.startswith("---") else text
                body_stripped = body.strip()
                chars = len(body_stripped)
                paras = len([p for p in body.split("\n\n") if p.strip()])
                images = list(out_dir.glob("*.jpg")) + list(out_dir.glob("*.png"))

                # 네비게이션 오인 방지 검증
                if not validate_article_body(body, title):
                    results[tid] = {
                        "status": "poisoned-navigation",
                        "reason": "네비게이션/본문 아님, 재수집 필요",
                        "url": url,
                        "chars": chars,
                    }
                    log(f"  -> POISONED (네비게이션), 재수집 필요")
                    json.dump(results, open(RESULTS_JSON, "w"), ensure_ascii=False, indent=2)
                    continue  # fall-through 방지

                # source_quality 게이트
                quality = None
                if HAS_QUALITY_GATE:
                    try:
                        quality = assess_capture(
                            text=body_stripped,
                            title=title,
                            url=url,
                            image_count=len(images),
                        )
                    except Exception as e:
                        log(f"  quality gate error: {e}")

                results[tid] = {
                    "status": "collected" if chars > 500 else "short",
                    "url": url,
                    "chars": chars,
                    "paragraphs": paras,
                    "images": len(images),
                    "bytes": size,
                    "quality": quality,
                }
                log(f"  -> {chars}자, {paras}단락, 이미지 {len(images)}개")
            else:
                results[tid] = {"status": "failed", "reason": "파일 미생성", "url": url}
                log(f"  -> FAILED (파일 미생성)")
        except subprocess.TimeoutExpired:
            results[tid] = {"status": "timeout", "url": url}
            log(f"  -> TIMEOUT")
        except Exception as e:
            results[tid] = {"status": "error", "reason": str(e)[:100], "url": url}
            log(f"  -> ERROR: {e}")

        # 즉시 저장 (resume)
        json.dump(results, open(RESULTS_JSON, "w"), ensure_ascii=False, indent=2)

    # 최종 통계
    from collections import Counter
    stats = Counter(r.get("status") for r in results.values())
    log(f"완료: {dict(stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
