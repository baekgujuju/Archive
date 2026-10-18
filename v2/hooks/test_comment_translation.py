"""댓글(답글) 한글 번역 테스트.

- needs_reply_translation: EN/JA/ZH, 한국어 전용, URL/티커 전용, 혼합 문장.
- reply_key 네임스페이스: 부모 키와 충돌 불가, 동일 답글 ID의 부모별 분리.
- collect_reply_jobs: 한국어 부모의 외국어 답글 수집, 캐시 유효 시 스킵,
  수정(해시 변경) 시 재수집, 실패 시 재수집.
- group_reply_batches: 문단/문자 한도 내 묶음.
- translate_reply_batch: 번호 경계 유지 + 답글별 분리 + 검증 실패 시 예외.
- translate_replies: bounded 배치, 답글별 캐시 엔트리, 실패 시 기존 유효
  캐시 보존 + 실패한 건만 재시도, 연속 실패 시 중단, 남은 백로그 수.
- 실제 provider·실제 Drive·프로덕션 state 파일을 건드리지 않는다
  (translate_paragraphs는 monkeypatch, 캐시는 메모리 dict + 임시 파일).
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent
V2_ROOT = HOOKS_DIR.parent
sys.path.insert(0, str(V2_ROOT))

import translate_posts as tp  # noqa: E402


def _reply(tweet_id, text, parent="111"):
    return {"tweetId": tweet_id, "fullText": text, "author": {"handle": "@u"}}


def _post(parent_id, replies, text="부모 글"):
    return {"tweetId": parent_id, "fullText": text, "replies": replies}


# --- needs_reply_translation ---

@pytest.mark.parametrize("text", [
    "This is a great point about memory.",
    "The Fed will cut rates next month, and markets are pricing it in.",
    "これはすごい発見だと思う。",
    "这个分析很重要，值得深入研究。",
    "이거 진짜 bullish하네, 내일 봐야겠다.",
    "FOMO가 온다, 일단 소액만.",
    "THIS IS GOOD, HOLD ON.",
    "DO NOT SELL, THIS IS A TRAP.",
    "RT @user hello world, this matters",
    "Check this out https://example.com/x — very interesting analysis here.",
    # 차단 5 (2차 리뷰): 기능어 없는 명령문도 산문.
    "STAY SAFE",
    "BUY LOW SELL HIGH",
    "TO THE MOON",
    "NOT FINANCIAL ADVICE",
    "GOOD MORNING",
    "I LOVE BTC",
    "U.S. ECONOMY",
    "A TRAP",
    "THIS IS GOOD 감사합니다",  # 한국어 혼합 문장의 영어 산문
])
def test_needs_translation_foreign(text):
    assert tp.needs_reply_translation(text) is True


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "이제 메모리로 뭉뚱그려도 안 되는 것",
    "삼성전자 오늘 좋네요",
    "https://example.com/only-a-link",
    "삼성전자 $005930 오늘 좋네요",
    "$BTC $ETH",
    "BTC",
    "GM",
    "lol",
    "🔥🔥🔥",
    "@someone",
    "#해시태그만",
    "BTC ETH",
    "BTC ETH SOL",
    "AAPL",
    # 차단 5 (2차 리뷰): 기능어만으로 구성된 대문자는 티커 나열로 본다.
    "IT CAN",
    "I CAN",
    "DO NOT",
    # 거래 쌍 기호는 티커로 본다.
    "BTCUSDT",
    "BTCUSDT ETHUSDT",
    # 한국어 + 티커 나열은 번역하지 않음.
    "BTC ETH 오를까",
])
def test_needs_translation_skip(text):
    assert tp.needs_reply_translation(text) is False


def test_mixed_korean_foreign_not_globally_skipped():
    # 한글이 있다는 이유만으로 전역 스킵하지 않음.
    assert tp.needs_reply_translation("한국어 문장 + This is foreign prose here") is True


def test_korean_plus_ticker_not_translated():
    # 일반 한국어 + 티커는 번역하지 않음.
    assert tp.needs_reply_translation("삼성전자 $005930 오늘 좋네요") is False
    assert tp.needs_reply_translation("이 종목 $AAPL 어때요?") is False


def test_attached_upper_word_not_stripped_as_ticker():
    # 한글에 붙은 대문자("FOMO가")는 독립 티커가 아니라 혼합 토큰의
    # 일부이므로 제거하지 않는다 (기존 \b 경계 동작 유지).
    assert tp.needs_reply_translation("FOMO가 온다, 일단 소액만.") is True
    # 독립 대문자 단어는 티커로 제거.
    assert tp.needs_reply_translation("이거 BTC 어때") is False


# --- reply_key 네임스페이스 ---

def test_reply_key_namespace_no_parent_collision():
    parent_id = "111"
    reply_id = "111"  # 답글 ID가 부모 ID와 같아도 키는 충돌하지 않음
    assert tp.reply_key(parent_id, reply_id) == "reply:111:111"
    assert tp.reply_key(parent_id, reply_id) != parent_id
    assert tp.reply_key(parent_id, reply_id) != reply_id


def test_reply_key_same_reply_different_parents():
    assert tp.reply_key("111", "999") != tp.reply_key("222", "999")


# --- collect_reply_jobs ---

def test_collect_korean_parent_foreign_reply():
    posts = [_post("111", [_reply("222", "This is a great point.")], text="한국어 부모 글")]
    jobs = tp.collect_reply_jobs(posts, {})
    assert len(jobs) == 1
    assert jobs[0]["key"] == "reply:111:222"


def test_collect_skips_korean_only_reply():
    posts = [_post("111", [_reply("222", "한국어 답글입니다.")])]
    assert tp.collect_reply_jobs(posts, {}) == []


def test_collect_skips_valid_cached():
    reply = _reply("222", "This is a great point.")
    posts = [_post("111", [reply])]
    cache = {"reply:111:222": {
        "paragraphs": ["This is a great point."],
        "translated": ["훌륭한 지적이다."],
        "content_hash": tp.content_hash("This is a great point."),
        "status": "ok",
    }}
    assert tp.collect_reply_jobs(posts, cache) == []


def test_collect_recollects_edited_reply():
    # 답글 수정 → 해시 변경 → 재수집 (오래된 번역 사용 금지).
    posts = [_post("111", [_reply("222", "This is a great point, edited.")])]
    cache = {"reply:111:222": {
        "paragraphs": ["This is a great point."],
        "translated": ["훌륭한 지적이다."],
        "content_hash": tp.content_hash("This is a great point."),
        "status": "ok",
    }}
    jobs = tp.collect_reply_jobs(posts, cache)
    assert len(jobs) == 1
    assert jobs[0]["content_hash"] == tp.content_hash("This is a great point, edited.")


def test_collect_recollects_failed_reply():
    posts = [_post("111", [_reply("222", "This is a great point.")])]
    cache = {"reply:111:222": {
        "paragraphs": ["This is a great point."],
        "translated": [],
        "content_hash": tp.content_hash("This is a great point."),
        "status": "failed",
    }}
    assert len(tp.collect_reply_jobs(posts, cache)) == 1


def test_collect_skips_reply_without_id():
    posts = [_post("111", [{"fullText": "This is a great point."}])]
    assert tp.collect_reply_jobs(posts, {}) == []


# --- group_reply_batches ---

def test_group_batches_respects_limits():
    jobs = []
    for i in range(5):
        jobs.append({
            "key": f"reply:1:{i}", "paragraphs": [f"Paragraph {i} here."],
            "real_indices": [0], "real_paras": [f"Paragraph {i} here."],
        })
    batches = tp.group_reply_batches(jobs)
    assert len(batches) == 1
    assert len(batches[0]) == 5


def test_group_batches_splits_on_paragraph_limit(monkeypatch):
    monkeypatch.setattr(tp, "REPLY_PROVIDER_MAX_PARAGRAPHS", 2)
    jobs = []
    for i in range(5):
        jobs.append({
            "key": f"reply:1:{i}", "paragraphs": [f"Paragraph {i} here."],
            "real_indices": [0], "real_paras": [f"Paragraph {i} here."],
        })
    batches = tp.group_reply_batches(jobs)
    assert [len(b) for b in batches] == [2, 2, 1]


def test_group_batches_single_oversized_job_kept():
    # group_reply_batches는 조각을 나누지 않는다 (분할은 split_reply_job이
    # 그룹핑 전에 수행). 한도 초과하는 단일 작업도 자기만의 배치로 유지.
    big = "x" * (tp.REPLY_PROVIDER_MAX_CHARS + 100)
    jobs = [{"key": "reply:1:1", "paragraphs": [big],
             "real_indices": [0], "real_paras": [big]}]
    batches = tp.group_reply_batches(jobs)
    assert len(batches) == 1 and batches[0][0]["key"] == "reply:1:1"


# --- translate_reply_batch ---

def test_batch_translation_keeps_reply_boundaries(monkeypatch):
    calls = []

    def fake_translate(paras, strict=False):
        calls.append(list(paras))
        assert strict is True  # 답글 배치는 strict 번호 검증 사용
        return [f"번역:{p}" for p in paras]

    monkeypatch.setattr(tp, "translate_paragraphs", fake_translate)
    batch = [
        {"key": "reply:1:11", "paragraphs": ["Hello world here.", "한국어 문단."],
         "real_indices": [0], "real_paras": ["Hello world here."]},
        {"key": "reply:1:22", "paragraphs": ["Good morning everyone."],
         "real_indices": [0], "real_paras": ["Good morning everyone."]},
    ]
    result = tp.translate_reply_batch(batch)
    # provider는 한 번만 호출되고, 두 답글 문단이 번호 순서대로 묶임.
    assert len(calls) == 1
    assert calls[0] == ["Hello world here.", "Good morning everyone."]
    # 답글별 위치 매핑으로 분리 (조각 재조립용).
    assert result["reply:1:11"] == {0: "번역:Hello world here."}
    assert result["reply:1:22"] == {0: "번역:Good morning everyone."}


def test_batch_translation_validation_failure(monkeypatch):
    monkeypatch.setattr(tp, "translate_paragraphs", lambda paras, strict=False: [""])
    batch = [{"key": "reply:1:11", "paragraphs": ["Hello world here."],
              "real_indices": [0], "real_paras": ["Hello world here."]}]
    with pytest.raises(RuntimeError):
        tp.translate_reply_batch(batch)


# --- 차단 2: 단일 작업 분할 + 재조립 ---

def test_split_reply_job_splits_oversized_job(monkeypatch):
    monkeypatch.setattr(tp, "REPLY_PROVIDER_MAX_PARAGRAPHS", 2)
    paras = [f"Paragraph {i} here." for i in range(5)]
    job = {"key": "reply:1:1", "paragraphs": paras,
           "real_indices": [0, 1, 2, 3, 4], "real_paras": paras,
           "parent_id": "1", "reply_id": "1", "text": "\n\n".join(paras),
           "content_hash": "abc"}
    frags = tp.split_reply_job(job)
    assert [len(f["real_indices"]) for f in frags] == [2, 2, 1]
    assert all(f["key"] == "reply:1:1" for f in frags)
    # 조각을 합치면 원본 real_indices 복원.
    assert [i for f in frags for i in f["real_indices"]] == [0, 1, 2, 3, 4]


def test_split_reply_job_keeps_fitting_job_whole():
    job = {"key": "reply:1:1", "paragraphs": ["Hello world here."],
           "real_indices": [0], "real_paras": ["Hello world here."]}
    assert tp.split_reply_job(job) == [job]


def test_translate_replies_reassembles_split_job(monkeypatch):
    # 30문단 답글 → 24+6 조각으로 나눠 번역 → 하나의 캐시 엔트리로 조립.
    monkeypatch.setattr(tp, "translate_paragraphs",
                        lambda paras, strict=False: [f"번역:{p}" for p in paras])
    monkeypatch.setattr(tp, "save_cache", lambda cache, touched: cache)
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    paras = [f"Paragraph number {i} here." for i in range(30)]
    posts = [_post("111", [_reply("222", "\n\n".join(paras))])]
    cache, touched = {}, set()
    success, failed, remaining = tp.translate_replies(
        cache, touched, posts=posts, max_per_run=20)
    assert (success, failed, remaining) == (1, 0, 0)
    entry = cache["reply:111:222"]
    assert entry["status"] == "ok"
    assert len(entry["translated"]) == 30
    assert entry["translated"][0] == "번역:Paragraph number 0 here."
    assert entry["translated"][29] == "번역:Paragraph number 29 here."


# --- 차단 2 (2차 리뷰): 단일 초과 문단 하위 조각 분할 + 재조립 ---

def test_split_long_paragraph_respects_char_limit():
    long_para = ("Sentence number one here. " * 400).strip()
    subs = tp._split_long_paragraph(long_para, tp.REPLY_PROVIDER_MAX_CHARS)
    assert len(subs) > 1
    assert all(len(s) <= tp.REPLY_PROVIDER_MAX_CHARS for s in subs)
    assert " ".join(subs) == long_para


def test_split_reply_job_splits_single_over_limit_paragraph():
    long_para = ("Sentence number one here. " * 400).strip()
    job = {"key": "reply:1:1", "paragraphs": [long_para],
           "real_indices": [0], "real_paras": [long_para],
           "parent_id": "1", "reply_id": "1", "text": long_para,
           "content_hash": "abc"}
    frags = tp.split_reply_job(job)
    assert len(frags) > 1
    # 모든 조각이 문단/문자 한도 내.
    assert all(len(f["real_paras"]) <= tp.REPLY_PROVIDER_MAX_PARAGRAPHS
               for f in frags)
    assert all(sum(len(p) for p in f["real_paras"]) <= tp.REPLY_PROVIDER_MAX_CHARS
               for f in frags)
    # 조각들을 순서대로 합치면 원본 문단 복원.
    assert " ".join(p for f in frags for p in f["real_paras"]) == long_para
    assert all(f["key"] == "reply:1:1" for f in frags)


def test_translate_replies_reassembles_over_limit_paragraph_across_batches(monkeypatch):
    # 초과 문단의 하위 조각이 여러 배치에 걸려도 하나의 문단으로 재조립.
    # (배치당 1문단으로 낮춰 조각이 배치 경계를 넘기게 한다.)
    monkeypatch.setattr(tp, "REPLY_PROVIDER_MAX_PARAGRAPHS", 1)
    monkeypatch.setattr(tp, "translate_paragraphs",
                        lambda paras, strict=False: [f"번역:{p}" for p in paras])
    monkeypatch.setattr(tp, "save_cache", lambda cache, touched: cache)
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    long_para = ("Sentence number one here. " * 400).strip()
    posts = [_post("111", [_reply("222", long_para)])]
    cache, touched = {}, set()
    success, failed, remaining = tp.translate_replies(
        cache, touched, posts=posts, max_per_run=20)
    assert (success, failed, remaining) == (1, 0, 0)
    entry = cache["reply:111:222"]
    assert entry["status"] == "ok"
    assert len(entry["translated"]) == 1
    expected = " ".join(
        f"번역:{s}" for s in tp._split_long_paragraph(
            long_para, tp.REPLY_PROVIDER_MAX_CHARS))
    assert entry["translated"][0] == expected


# --- 차단 3: strict 번호 검증 ---

def test_translate_paragraphs_strict_duplicate_number(monkeypatch):
    monkeypatch.setattr(tp, "run_translation_provider",
                        lambda prompt: "[1] 첫 번째\n[2] 두 번째\n[1] 중복")
    with pytest.raises(RuntimeError, match="중복"):
        tp.translate_paragraphs(["Para one here.", "Para two here."], strict=True)


def test_translate_paragraphs_strict_out_of_range(monkeypatch):
    monkeypatch.setattr(tp, "run_translation_provider",
                        lambda prompt: "[1] 첫 번째\n[99] 범위 밖")
    with pytest.raises(RuntimeError, match="범위 밖"):
        tp.translate_paragraphs(["Para one here."], strict=True)


def test_translate_paragraphs_strict_missing_number(monkeypatch):
    monkeypatch.setattr(tp, "run_translation_provider",
                        lambda prompt: "[1] 첫 번째")
    with pytest.raises(RuntimeError, match="누락"):
        tp.translate_paragraphs(["Para one here.", "Para two here."], strict=True)


def test_translate_paragraphs_lax_mode_unchanged(monkeypatch):
    # 부모 경로(strict=False)는 기존 동작 유지: 중복은 마지막 값으로 덮음.
    monkeypatch.setattr(tp, "run_translation_provider",
                        lambda prompt: "[1] 첫 번째\n[2] 두 번째\n[1] 덮어씀")
    assert tp.translate_paragraphs(["Para one here.", "Para two here."]) == [
        "덮어씀", "두 번째"]


# --- 차단 1: 실패 쿨다운 ---

def _failed_cache(text, failed_at):
    return {"reply:111:222": {
        "paragraphs": [text],
        "translated": [],
        "content_hash": tp.content_hash(text),
        "status": "failed",
        "failed_at": failed_at,
    }}


def test_collect_skips_recently_failed_reply_in_cooldown():
    from datetime import datetime, timezone
    posts = [_post("111", [_reply("222", "This is a great point.")])]
    cache = _failed_cache("This is a great point.",
                          datetime.now(timezone.utc).isoformat())
    assert tp.collect_reply_jobs(posts, cache) == []


def test_collect_retries_failed_reply_after_cooldown():
    posts = [_post("111", [_reply("222", "This is a great point.")])]
    cache = _failed_cache("This is a great point.", "2020-01-01T00:00:00+00:00")
    assert len(tp.collect_reply_jobs(posts, cache)) == 1


def test_cooldown_lets_later_backlog_proceed():
    from datetime import datetime, timezone
    posts = [_post("111", [_reply("222", "This is a great point."),
                            _reply("333", "Another point here.")])]
    cache = _failed_cache("This is a great point.",
                          datetime.now(timezone.utc).isoformat())
    jobs = tp.collect_reply_jobs(posts, cache)
    assert [j["key"] for j in jobs] == ["reply:111:333"]


# --- 차단 1 (2차 리뷰): 쿨다운/백로그가 남으면 마커 유지를 위해 남은 수에 포함 ---

def test_collect_counts_cooldown_skipped_in_stats():
    from datetime import datetime, timezone
    posts = [_post("111", [_reply("222", "This is a great point.")])]
    cache = _failed_cache("This is a great point.",
                          datetime.now(timezone.utc).isoformat())
    stats = {}
    assert tp.collect_reply_jobs(posts, cache, stats=stats) == []
    assert stats["cooldown_skipped"] == 1


def test_translate_replies_cooldown_only_counts_as_remaining(monkeypatch):
    # 쿨다운만 남은 경우: 성공/실패 0건이지만 남은 백로그 1건으로 보고해
    # 후크가 재시도 마커를 유지하고 쿨다운 만료 후 재시도하게 한다.
    from datetime import datetime, timezone
    posts = [_post("111", [_reply("222", "This is a great point.")])]
    cache = _failed_cache("This is a great point.",
                          datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(tp, "save_cache", lambda cache, touched: cache)
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    assert tp.translate_replies(dict(cache), set(), posts=posts) == (0, 0, 1)


def test_cooldown_naive_timestamp_does_not_raise():
    # 시간대 없는 ISO 문자열(구 캐시 등)도 TypeError 없이 처리.
    from datetime import datetime
    posts = [_post("111", [_reply("222", "This is a great point.")])]
    cache = _failed_cache("This is a great point.",
                          datetime.now().isoformat())  # naive
    stats = {}
    assert tp.collect_reply_jobs(posts, cache, stats=stats) == []
    assert stats["cooldown_skipped"] == 1


# --- translate_replies (orchestrator) ---

def test_translate_replies_bounded_and_per_reply_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(tp, "translate_paragraphs",
                        lambda paras, strict=False: [f"번역:{p}" for p in paras])
    monkeypatch.setattr(tp, "save_cache", lambda cache, touched: cache)
    posts = [_post("111", [_reply(str(100 + i), f"Reply number {i} here.")
                            for i in range(5)])]
    cache, touched = {}, set()
    # sleep 우회: translate_replies 내부 time.sleep을 no-op으로.
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    success, failed, remaining = tp.translate_replies(
        cache, touched, posts=posts, max_per_run=3)
    assert success == 3 and failed == 0 and remaining == 2
    # 답글별 캐시 엔트리 유지 (배치 하나로 묶지 않음).
    assert sum(1 for k in cache if k.startswith("reply:")) == 3
    for k, v in cache.items():
        assert v["status"] == "ok"
        assert v["content_hash"] == tp.content_hash(
            next(r["fullText"] for r in posts[0]["replies"]
                 if tp.reply_key("111", r["tweetId"]) == k))


def test_translate_replies_failure_preserves_prior_ok(monkeypatch):
    def boom(paras, strict=False):
        raise RuntimeError("provider down")

    monkeypatch.setattr(tp, "translate_paragraphs", boom)
    monkeypatch.setattr(tp, "save_cache", lambda cache, touched: cache)
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    posts = [_post("111", [_reply("222", "This is a great point, edited.")])]
    prior = {
        "paragraphs": ["This is a great point."],
        "translated": ["훌륭한 지적이다."],
        "content_hash": tp.content_hash("This is a great point."),
        "status": "ok",
    }
    cache, touched = {"reply:111:222": dict(prior)}, set()
    success, failed, remaining = tp.translate_replies(
        cache, touched, posts=posts, max_per_run=20)
    assert success == 0 and failed == 1 and remaining == 1
    # 기존 유효 캐시 보존 (last_attempt에 실패 기록만 추가).
    kept = cache["reply:111:222"]
    assert kept["status"] == "ok"
    assert kept["translated"] == ["훌륭한 지적이다."]
    assert kept["last_attempt"]["status"] == "failed"


def test_translate_replies_stops_after_two_consecutive_batch_failures(monkeypatch):
    calls = []

    def boom(paras, strict=False):
        calls.append(1)
        raise RuntimeError("provider down")

    monkeypatch.setattr(tp, "translate_paragraphs", boom)
    monkeypatch.setattr(tp, "save_cache", lambda cache, touched: cache)
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    monkeypatch.setattr(tp, "REPLY_PROVIDER_MAX_PARAGRAPHS", 1)  # 배치당 1건 → 3배치
    posts = [_post("111", [_reply(str(100 + i), f"Reply number {i} here.")
                            for i in range(3)])]
    cache, touched = {}, set()
    success, failed, remaining = tp.translate_replies(
        cache, touched, posts=posts, max_per_run=20)
    # 2회 연속 배치 실패 후 중단 → 3번째 배치는 시도하지 않음.
    assert len(calls) == 2
    assert success == 0 and failed == 2 and remaining == 3


def test_translate_replies_empty_posts():
    cache, touched = {}, set()
    assert tp.translate_replies(cache, touched, posts=[]) == (0, 0, 0)


def test_main_processes_replies_when_no_parent_jobs(monkeypatch, tmp_path):
    # 부모 작업 없음(한국어 부모 글만) + 답글 작업 있음 → 답글이 처리돼야 함.
    # (early return이 답글 단계를 건너뛰면 안 됨 — 새 글 없어도 백로그 계속 처리.)
    posts = {"posts": [_post("111", [_reply("222", "This is a great point.")],
                              text="한국어 부모 글")]}
    (tmp_path / "posts-x.json").write_text(
        json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tp, "DATA_DIR", tmp_path)
    saved = {}
    monkeypatch.setattr(tp, "load_cache", lambda: {})

    def fake_save(cache, touched):
        saved.update(cache)
        return cache

    monkeypatch.setattr(tp, "save_cache", fake_save)
    monkeypatch.setattr(tp, "translate_paragraphs",
                        lambda paras, strict=False: [f"번역:{p}" for p in paras])
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["translate_posts.py"])
    assert tp.main() == 0
    assert "reply:111:222" in saved
    assert saved["reply:111:222"]["status"] == "ok"


def test_main_returns_1_when_reply_backlog_remains(monkeypatch, tmp_path):
    # 백로그가 남으면 1 반환 → 후크가 재시도 마커를 유지해 다음 폴이
    # 백로그를 이어서 처리한다 (0이면 마커 삭제 → 백로그 정체).
    posts = {"posts": [_post("111", [
        _reply("222", "This is a great point."),
        _reply("333", "Another great point here."),
    ], text="한국어 부모 글")]}
    (tmp_path / "posts-x.json").write_text(
        json.dumps(posts, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tp, "DATA_DIR", tmp_path)
    saved = {}
    monkeypatch.setattr(tp, "load_cache", lambda: {})
    monkeypatch.setattr(tp, "save_cache",
                        lambda cache, touched: saved.update(cache) or cache)
    monkeypatch.setattr(tp, "translate_paragraphs",
                        lambda paras, strict=False: [f"번역:{p}" for p in paras])
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["translate_posts.py", "--reply-batch", "1"])
    assert tp.main() == 1
    assert len(saved) == 1
    # 두 번째 폴: 남은 1건 처리 → 전부 완료 → 0.
    saved2 = {}
    monkeypatch.setattr(tp, "load_cache", lambda: dict(saved))
    monkeypatch.setattr(tp, "save_cache",
                        lambda cache, touched: saved2.update(cache) or cache)
    monkeypatch.setattr(sys, "argv", ["translate_posts.py"])
    assert tp.main() == 0
    assert "reply:111:333" in saved2
