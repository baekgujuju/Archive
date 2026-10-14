"""Validate extracted article evidence; never classify a long page as full by length."""
import re

def normalized(value):
    return re.sub(r'[^\w가-힣]+', '', str(value or '').lower())

def assess_capture(record, capture):
    reasons = []
    body = str(capture.get('body_text') or '').strip()
    if capture.get('error'):
        return {'status': 'failed', 'reasons': ['capture_error']}
    if not body:
        return {'status': 'failed', 'reasons': ['empty_body']}
    if capture.get('extraction_scope') != 'article':
        reasons.append('not_article_scope')
    if not capture.get('content_selector'):
        reasons.append('missing_body_selector_evidence')
    title = normalized(record.get('title'))
    actual = normalized(capture.get('source_title'))
    if not title or not actual or not (title in actual or actual in title):
        reasons.append('source_title_mismatch')
    nav = ['카페정보', '카페멤버수', '카페 가입하기', '전체글보기', '게시판 목록 바로가기', '카페 스마트봇']
    if sum(word in body for word in nav) >= 3:
        reasons.append('cafe_navigation_in_body')
    if capture.get('access_state') in ['login_required', 'membership_required', 'captcha', 'paywall', 'deleted']:
        reasons.append('access_blocked')
    paragraphs = [str(p).strip() for p in capture.get('paragraphs', []) if str(p).strip()]
    if not paragraphs:
        reasons.append('missing_paragraph_evidence')
    else:
        compact = normalized(body)
        if normalized(paragraphs[0]) not in compact or normalized(paragraphs[-1]) not in compact:
            reasons.append('body_boundaries_missing')
    images = capture.get('images', [])
    missing = [image for image in images if not image.get('local_path') or not image.get('sha256') or not image.get('verified_file')]
    if missing:
        reasons.append('image_files_missing')
    if not capture.get('capture_complete'):
        reasons.append('not_confirmed_complete')
    # Private text is retained outside this status record and never copied into logs.
    return {'status': 'needs_review' if reasons else 'full', 'reasons': reasons,
            'text_chars': len(body), 'paragraph_count': len(paragraphs),
            'images_expected': len(images), 'images_saved': len(images)-len(missing)}
