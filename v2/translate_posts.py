#!/usr/bin/env python3
"""
v2 번역 스크립트 (수정판)

영어 전용 게시글을 문단별로 한국어로 번역.
대상: posts-x.json + posts-web.json + posts-ib.json

개선사항 (audit 반영):
  - content_hash 기반: 해시 일치 시 스킵, 불일치 시 재번역
  - 원문 문단 보존 (paragraphs[])
  - 멀티라인 번역 출력 처리 (다음 [N] 마커까지 누적)
  - "Click to Follow/Subscribe" 플레이스홀더 제외
  - 이미 이중언어(Oliver 등)는 is_english_only로 자동 제외 → 중복 방지

사용법:
    python3 translate_posts.py              # 번역 실행 (캐시 사용)
    python3 translate_posts.py --force       # 캐시 무시하고 다시 번역

번역 캐시: v2/data/translations.json
  post_id → {"paragraphs": [...], "translated": [...],
             "content_hash": "sha256앞16자", "status": "ok",
             "translated_at": "ISO8601"}
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

V2_ROOT = Path(__file__).parent
DATA_DIR = V2_ROOT / "data"
CACHE_FILE = DATA_DIR / "translations.json"
API_KEY_PATH = Path.home() / ".gemini" / ".api_key"

KO_PAT = re.compile('[가-힣]')
# 번역 제외 플레이스홀더 (X 임베드 푸터 등)
SKIP_PATTERNS = [
    re.compile(r'click\s+to\s+(follow|subscribe)', re.IGNORECASE),
    re.compile(r'^(follow|subscribe)\s*$', re.IGNORECASE),
]

# 번역 대상 청크
TARGET_CHUNKS = ["posts-x.json", "posts-web.json", "posts-ib.json"]


def is_english_only(text):
    """한국어가 전혀 없는 텍스트인지."""
    if not text or not text.strip():
        return False
    return not KO_PAT.search(text)


def is_placeholder(para):
    """번역 제외 플레이스홀더인지."""
    p = para.strip()
    return any(pat.search(p) for pat in SKIP_PATTERNS)


def split_paragraphs(text):
    """문단 분리 (빈 줄 기준). 플레이스홀더는 별도 표시."""
    paras = [p.strip() for p in text.split('\n\n')]
    return [p for p in paras if p]


def content_hash(text):
    """원문 해시 (앞 16자)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def translate_paragraphs(paragraphs):
    """문단 리스트를 한국어로 번역 (Gemini API).

    멀티라인 출력 처리: [N] 마커로 시작하는 줄부터 다음 [N] 마커 전까지를
    하나의 번역문으로 누적.
    """
    if not API_KEY_PATH.exists():
        raise FileNotFoundError("Gemini API key not found")

    numbered = "\n".join(f"[{i+1}] {p}" for i, p in enumerate(paragraphs))
    prompt = f"""다음 외국어 문단들을 한국어로 번역해주세요.
각 문단 번호를 유지하고, 번역만 출력하세요.
형식: [번호] 번역문
번역문이 여러 줄이어도 됩니다. 다음 번호가 나오기 전까지가 하나의 번역문입니다.

{numbered}"""

    env = os.environ.copy()
    env["GEMINI_API_KEY"] = API_KEY_PATH.read_text().strip()
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

    result = subprocess.run(
        ["gemini", "-m", "gemini-3.6-flash", "-p", prompt],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Gemini API 실패: {result.stderr[:500]}")

    # 멀티라인 파싱: [N] 마커까지 누적
    output = result.stdout.strip()
    translated = [""] * len(paragraphs)
    current_idx = -1
    current_lines = []

    def flush():
        nonlocal current_idx, current_lines
        if current_idx >= 0 and current_idx < len(paragraphs):
            translated[current_idx] = "\n".join(current_lines).strip()
        current_idx = -1
        current_lines = []

    for line in output.split('\n'):
        m = re.match(r'\[(\d+)\]\s*(.*)', line.strip())
        if m:
            flush()
            current_idx = int(m.group(1)) - 1
            current_lines = [m.group(2)] if m.group(2) else []
        elif current_idx >= 0:
            # 이전 번호의 계속 (멀티라인)
            current_lines.append(line)
    flush()

    # 개수 검증
    empty = sum(1 for t in translated if not t.strip())
    if empty:
        print(f"  경고: 빈 번역 {empty}개 (전체 {len(paragraphs)})", file=sys.stderr)

    return translated


def get_post_text(post):
    """포스트에서 번역 대상 텍스트 추출."""
    if post.get('fullHtml'):
        parser = ArticleParagraphs()
        parser.feed(post['fullHtml'])
        if parser.paragraphs:
            return '\n\n'.join(parser.paragraphs)
    # IB는 fullText, X는 fullText, Web은 fullText/text
    for key in ("fullText", "text", "summary"):
        t = post.get(key, "")
        if t and isinstance(t, str) and t.strip():
            return t
    return ""


class ArticleParagraphs(HTMLParser):
    """Leaf prose blocks: preserve actual HTML paragraph boundaries."""
    BLOCKS = {'p','h1','h2','h3','h4','h5','h6','li','blockquote','figcaption','td','th'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.paragraphs, self.skip = [], [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}: self.skip += 1
        if tag in self.BLOCKS:
            for block in self.stack: block['nested'] = True
            self.stack.append({'tag':tag,'parts':[],'nested':False})
        if tag == 'br': self.handle_data('\n')
    def handle_data(self, data):
        if not self.skip:
            for block in self.stack: block['parts'].append(data)
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.skip = max(0,self.skip-1)
        if self.stack and self.stack[-1]['tag'] == tag:
            block=self.stack.pop()
            value=''.join(block['parts']).strip()
            if value and not block['nested']: self.paragraphs.append(value)


def main():
    parser = argparse.ArgumentParser(description='영어 게시글 문단별 번역 (X/Web/IB)')
    parser.add_argument('--force', action='store_true', help='캐시 무시하고 다시 번역')
    args = parser.parse_args()

    cache = {}
    if CACHE_FILE.exists() and not args.force:
        with open(CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
        print(f"캐시 로드: {len(cache)}개")

    # 전체 청크에서 번역 대상 수집
    to_translate = []  # (post_id, text, chunk)
    skipped_hash = 0
    skipped_ko = 0
    for chunk_name in TARGET_CHUNKS:
        chunk_path = DATA_DIR / chunk_name
        if not chunk_path.exists():
            continue
        with open(chunk_path, encoding='utf-8') as f:
            data = json.load(f)
        posts = data.get('posts', []) if isinstance(data, dict) else []

        for post in posts:
            if not isinstance(post, dict):
                continue
            post_id = post.get('id') or post.get('tweetId') or str(post.get('_idx', ''))
            if not post_id:
                continue
            text = get_post_text(post)
            if not any(is_english_only(p) and not is_placeholder(p) for p in split_paragraphs(text)):
                skipped_ko += 1
                continue

            h = content_hash(text)
            cached = cache.get(post_id)
            if cached and not args.force:
                # 해시 일치 시 스킵, 불일치 시 재번역
                valid_cached = (cached.get('paragraphs') == split_paragraphs(text)
                                and isinstance(cached.get('translated'), list)
                                and len(cached['translated']) == len(cached['paragraphs'])
                                and all(isinstance(t,str) and t.strip() for t in cached['translated']))
                if cached.get("content_hash") == h and cached.get("status") == "ok" and valid_cached:
                    skipped_hash += 1
                    continue
                if cached.get("content_hash") != h:
                    print(f"  해시 변경 → 재번역: {post_id[:40]}")

            to_translate.append((post_id, text, chunk_name))

    print(f"번역 대상: {len(to_translate)}개 "
          f"(해시스킵 {skipped_hash}, 한국어포함스킵 {skipped_ko})")
    if not to_translate:
        print("번역할 글이 없습니다.")
        return 0

    success, failed = 0, 0
    for i, (post_id, text, chunk_name) in enumerate(to_translate):
        print(f"\n[{i+1}/{len(to_translate)}] [{chunk_name}] {post_id[:40]}...")
        paragraphs = split_paragraphs(text)
        # 플레이스홀더 제외 (번역은 하되 마킹)
        real_paras = [p for p in paragraphs if is_english_only(p) and not is_placeholder(p)]
        skipped_ph = len(paragraphs) - len(real_paras)
        if skipped_ph:
            print(f"  플레이스홀더 제외: {skipped_ph}개")
        if not real_paras:
            print(f"  번역할 문단 없음 — 스킵")
            continue
        print(f"  문단 수: {len(real_paras)}")

        try:
            translated = translate_paragraphs(real_paras)
            if len(translated) != len(real_paras) or any(not p.strip() for p in translated):
                raise RuntimeError('번역 문단 누락: 완료로 기록하지 않고 재시도합니다.')
            # 원문 순서대로 매핑 (플레이스홀더는 원문 유지)
            full_translated = []
            ri = 0
            for p in paragraphs:
                if is_placeholder(p) or not is_english_only(p):
                    full_translated.append(p)  # 플레이스홀더는 번역하지 않음
                else:
                    full_translated.append(translated[ri] if ri < len(translated) else "")
                    ri += 1

            from datetime import datetime, timezone
            cache[post_id] = {
                "paragraphs": paragraphs,  # 원문 보존
                "translated": full_translated,
                "content_hash": content_hash(text),
                "status": "ok",
                "translated_at": datetime.now(timezone.utc).isoformat(),
                "chunk": chunk_name,
            }
            success += 1
            print(f"  ✓ 번역 완료")

            if (i + 1) % 5 == 0:
                with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
                print(f"  (캐시 중간 저장)")

            time.sleep(2)
        except Exception as e:
            print(f"  ✗ 실패: {e}", file=sys.stderr)
            failed += 1
            # 실패 기록 (다음 실행에서 재시도)
            failure = {
                "paragraphs": paragraphs,
                "translated": [],
                "content_hash": content_hash(text),
                "status": "failed",
                "error": str(e)[:200],
            }
            previous=cache.get(post_id)
            if previous and previous.get('status') == 'ok':
                # Keep the last successful version; its old hash still triggers a retry.
                cache[post_id] = dict(previous, last_attempt=failure)
            else:
                cache[post_id] = failure
            time.sleep(5)

    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"\n=== 완료 ===")
    print(f"성공: {success}개, 실패: {failed}개")
    print(f"캐시 파일: {CACHE_FILE}")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
