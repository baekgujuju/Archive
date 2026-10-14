#!/usr/bin/env python3
"""
Naver/웹 원문 수집 스크립트.
- private-sources/ 에 저장 (공개 GitHub에 커밋하지 않음)
- targets.json 기반, resume 가능
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


def is_navigation_content(text):
    """
    네비게이션 오인 방지 (2026-10-14 대표님 지시).
    카페 정보·멤버수·게시판 목록이 주 내용이면 False.
    """
    nav_markers = ["카페정보", "카페멤버수", "게시판", "카페등급"]
    nav_count = sum(1 for m in nav_markers if m in text[:2000])
    # 실제 글 제목 패턴 확인
    return nav_count >= 3


def validate_article_body(text, expected_title=""):
    """
    실제 글 본문인지 검증.
    - 네비게이션이면 False
    - 본문이 500자 미만이면 False
    """
    if not text or len(text.strip()) < 500:
        return False
    if is_navigation_content(text):
        return False
    return True


def main():
    targets = json.load(open(TARGETS_JSON))
    results = {}
    if RESULTS_JSON.exists():
        results = json.load(open(RESULTS_JSON))

    pending = [t for t in targets if t["id"] not in results]
    log(f"전체 {len(targets)}, 완료 {len(results)}, 남은 {len(pending)}")

    for t in pending:
        tid = t["id"]
        url = t["url"]
        if not url:
            results[tid] = {"status": "no-url", "reason": "URL 없음"}
            continue

        out_dir = PRIVATE_DIR / tid
        out_dir.mkdir(parents=True, exist_ok=True)
        out_md = out_dir / "article.md"

        log(f"수집 중: {tid} ({t['title'][:30]})")
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
                # 네비게이션 오인 방지 검증
                if not validate_article_body(body, t.get("title", "")):
                    results[tid] = {
                        "status": "poisoned-navigation",
                        "reason": "네비게이션/본문 아님, 재수집 필요",
                        "url": url,
                    }
                    log(f"  -> POISONED (네비게이션), 재수집 필요")
                else:
                    chars = len(body.strip())
                paras = len([p for p in body.split("\n\n") if p.strip()])
                # 이미지 수
                images = list(out_dir.glob("*.jpg")) + list(out_dir.glob("*.png"))
                results[tid] = {
                    "status": "collected" if chars > 500 else "short",
                    "url": url,
                    "chars": chars,
                    "paragraphs": paras,
                    "images": len(images),
                    "bytes": size,
                }
                log(f"  -> {chars}자, {paras}단락, 이미지 {len(images)}개")
            else:
                results[tid] = {"status": "failed", "reason": "파일 미생성", "url": url}
        except subprocess.TimeoutExpired:
            results[tid] = {"status": "timeout", "url": url}
        except Exception as e:
            results[tid] = {"status": "error", "reason": str(e)[:100], "url": url}

        # 즉시 저장 (resume)
        json.dump(results, open(RESULTS_JSON, "w"), ensure_ascii=False, indent=2)

    # 최종 통계
    from collections import Counter
    stats = Counter(r.get("status") for r in results.values())
    log(f"완료: {dict(stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
