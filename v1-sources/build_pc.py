#!/usr/bin/env python3
"""PC 전용 빌드: 원본 해상도 이미지 내장 (용량 무관)."""
import sys
sys.path.insert(0, "/home/hatch/workspace/article-archive")

import build_dashboard as bd
from pathlib import Path
import base64

def full_image_original(cache_key, originals):
    """원본 해상도 그대로 base64로 (리사이즈 없음)."""
    source = next((p for p in sorted(originals.glob(f"{cache_key}.*"))
                   if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if source is None:
        raise FileNotFoundError(f"원본 없음: {originals / cache_key}")
    # 원본 그대로 읽기 (변환 없이)
    data = source.read_bytes()
    # PNG면 그대로, 아니면 JPEG로
    if source.suffix.lower() == ".png":
        mime = "image/png"
    else:
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")

# 원본 해상도 함수로 교체
bd.full_image = full_image_original

if __name__ == "__main__":
    from pathlib import Path
    import re
    # PC 빌드: build()를 호출하되 출력 경로만 다르게
    import sys
    bd_dir = Path("/home/hatch/workspace/article-archive")
    idx = bd_dir / "index.json"
    # index.json이 없으면 goals 경로에서 찾기
    if not idx.is_file():
        idx = Path("/home/hatch/workspace/goals/goal-10/files/index.json")
    out = Path("/home/hatch/workspace/goals/goal-10/files/웹글-아카이브-PC.html")
    bd.build(idx, out)
    # PC 전용 후처리: 원문보기 링크 제거 (뷰어에서 동작 안 함)
    html = out.read_text(encoding="utf-8")
    html = re.sub(r'<a class="read-link"[^>]*>원문 보기[^<]*</a>', '', html)
    # Cycle 글에 한국어 교육자료 링크 추가
    study_link = '<div style="margin:16px 0;padding:12px;background:#f0f7f0;border-radius:8px;"><a href="sandbox://workspace/your_files/cycle-of-price-action-study.html" target="_blank" style="color:#17675c;font-weight:700;">📚 한국어 교육자료 보기 (Cycle of Price Action 학습 자료)</a></div>'
    html = html.replace('Cycle Of Price Action</h3>', 'Cycle Of Price Action</h3>' + study_link, 1)
    out.write_text(html, encoding="utf-8")
    print(f"PC 버전 생성: {out} ({out.stat().st_size / 1024 / 1024:.1f}MB)")
