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
from urllib.parse import urlparse
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
    host = (urlparse(url).hostname or '').lower()
    return host == 'naver.me' or host == 'naver.com' or host.endswith('.naver.com')


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
    - 본문이 비어 있으면 False
    - 제목은 실제 source_title 증거로 quality gate에서 대조
    """
    if not text or not text.strip():
        return False
    if is_navigation_content(text):
        return False
    return True


def main():
    targets = json.loads(TARGETS_JSON.read_text(encoding="utf-8"))
    results = {}
    if RESULTS_JSON.exists():
        results = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))

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
            RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
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
            if r.returncode != 0:
                results[tid] = {"status": "failed", "reason": "fetch_process_failed", "url": url}
                RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
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
                    RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                    continue  # fall-through 방지

                # Missing or incompatible evidence must never pass the gate.
                evidence_path = out_dir / "capture-evidence.json"
                capture = json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.exists() else {}
                capture["body_text"] = body_stripped
                if not HAS_QUALITY_GATE:
                    quality = {"status": "needs_review", "reasons": ["quality_gate_unavailable"]}
                else:
                    quality = assess_capture({"title": title, "source_url": url}, capture)
                results[tid] = {
                    "status": quality["status"], "url": url, "chars": chars,
                    "paragraphs": paras, "images": len(images), "bytes": size,
                    "quality": quality, "collector_revision": "fail-closed-255",
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
        RESULTS_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 최종 통계
    from collections import Counter
    stats = Counter(r.get("status") for r in results.values())
    log(f"완료: {dict(stats)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
