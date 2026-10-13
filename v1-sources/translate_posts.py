#!/usr/bin/env python3
"""
v2 번역 스크립트
영어 전용 게시글을 문단별로 한국어로 번역

사용법:
    python3 translate_posts.py              # 번역 실행 (캐시 사용)
    python3 translate_posts.py --force       # 캐시 무시하고 다시 번역

번역 캐시: v2/data/translations.json
- post_id → {"paragraphs": [...], "translated": [...]}
- 한 번 번역하면 다음부터 재사용
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

V2_ROOT = Path(__file__).parent
DATA_DIR = V2_ROOT / "data"
CACHE_FILE = DATA_DIR / "translations.json"

KO_PAT = re.compile('[가-힣]')


def is_english_only(text):
    """한국어가 전혀 없는 텍스트인지"""
    if not text or not text.strip():
        return False
    return not KO_PAT.search(text)


def split_paragraphs(text):
    """문단 분리 (빈 줄 기준)"""
    paras = [p.strip() for p in text.split('\n\n')]
    return [p for p in paras if p]


def translate_paragraphs(paragraphs):
    """
    문단 리스트를 한국어로 번역 (Gemini API)

    Args:
        paragraphs: 영어 문단 리스트

    Returns:
        list: 번역된 한국어 문단 리스트 (순서 동일)
    """
    api_key = Path.home() / ".gemini" / ".api_key"
    if not api_key.exists():
        raise FileNotFoundError("Gemini API key not found")

    # 모든 문단을 하나의 프롬프트로 (번호 매겨서)
    numbered = "\n".join(f"[{i+1}] {p}" for i, p in enumerate(paragraphs))

    prompt = f"""다음 영어 문단들을 한국어로 번역해주세요.
각 문단 번호를 유지하고, 번역만 출력하세요.
형식: [번호] 번역문

{numbered}"""

    # Gemini CLI 호출
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = api_key.read_text().strip()
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"

    result = subprocess.run(
        ["gemini", "-m", "gemini-3.6-flash", "-p", prompt],
        capture_output=True,
        text=True,
        env=env,
        timeout=120
    )

    if result.returncode != 0:
        raise RuntimeError(f"Gemini API 실패: {result.stderr[:500]}")

    # 응답 파싱: [1] 번역문 형식
    output = result.stdout.strip()
    translated = []
    for line in output.split('\n'):
        line = line.strip()
        m = re.match(r'\[(\d+)\]\s*(.*)', line)
        if m:
            idx = int(m.group(1)) - 1
            # 인덱스에 맞게 삽입
            while len(translated) <= idx:
                translated.append("")
            translated[idx] = m.group(2)

    # 개수 확인
    if len(translated) != len(paragraphs):
        print(f"  경고: 번역 개수 불일치 (원문 {len(paragraphs)}, 번역 {len(translated)})",
              file=sys.stderr)

    return translated


def main():
    parser = argparse.ArgumentParser(description='영어 게시글 문단별 번역')
    parser.add_argument('--force', action='store_true', help='캐시 무시하고 다시 번역')
    args = parser.parse_args()

    # 캐시 로드
    cache = {}
    if CACHE_FILE.exists() and not args.force:
        with open(CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
        print(f"캐시 로드: {len(cache)}개")

    # posts-x.json 로드
    posts_path = DATA_DIR / "posts-x.json"
    with open(posts_path, encoding='utf-8') as f:
        data = json.load(f)
    posts = data['posts']

    # 영어 전용 글 찾기
    to_translate = []
    for post in posts:
        post_id = post.get('id') or post.get('tweetId') or str(post.get('_idx', ''))
        text = post.get('fullText', '') or post.get('text', '')

        if not is_english_only(text):
            continue

        if post_id in cache and not args.force:
            continue

        to_translate.append((post_id, text))

    print(f"번역 대상: {len(to_translate)}개 (전체 {len(posts)}개 중)")

    if not to_translate:
        print("번역할 글이 없습니다.")
        return

    # 번역 실행
    success = 0
    failed = 0
    for i, (post_id, text) in enumerate(to_translate):
        print(f"\n[{i+1}/{len(to_translate)}] {post_id[:30]}...")

        paragraphs = split_paragraphs(text)
        print(f"  문단 수: {len(paragraphs)}")

        try:
            translated = translate_paragraphs(paragraphs)
            cache[post_id] = {
                "paragraphs": paragraphs,
                "translated": translated,
            }
            success += 1
            print(f"  ✓ 번역 완료")

            # 중간 저장 (실패 시에도 캐시 유지)
            if (i + 1) % 5 == 0:
                with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(cache, f, ensure_ascii=False, indent=2)
                print(f"  (캐시 중간 저장)")

            # API 레이트 리밋 방지
            time.sleep(2)

        except Exception as e:
            print(f"  ✗ 실패: {e}", file=sys.stderr)
            failed += 1
            time.sleep(5)  # 실패 시 더 길게 대기

    # 최종 캐시 저장
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"\n=== 완료 ===")
    print(f"성공: {success}개, 실패: {failed}개")
    print(f"캐시 파일: {CACHE_FILE}")


if __name__ == '__main__':
    main()
