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
    "RT @user hello world, this matters",
    "Check this out https://example.com/x — very interesting analysis here.",
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
    # 한도 초과하는 단일 작업도 자기만의 배치로 유지 (버려지지 않음).
    big = "x" * (tp.REPLY_PROVIDER_MAX_CHARS + 100)
    jobs = [{"key": "reply:1:1", "paragraphs": [big],
             "real_indices": [0], "real_paras": [big]}]
    batches = tp.group_reply_batches(jobs)
    assert len(batches) == 1 and batches[0][0]["key"] == "reply:1:1"


# --- translate_reply_batch ---

def test_batch_translation_keeps_reply_boundaries(monkeypatch):
    calls = []

    def fake_translate(paras):
        calls.append(list(paras))
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
    # 답글별 분리: 한국어 문단은 원문 유지, 번역 문단은 답글별로 배정.
    assert result["reply:1:11"] == ["번역:Hello world here.", "한국어 문단."]
    assert result["reply:1:22"] == ["번역:Good morning everyone."]


def test_batch_translation_validation_failure(monkeypatch):
    monkeypatch.setattr(tp, "translate_paragraphs", lambda paras: [""])
    batch = [{"key": "reply:1:11", "paragraphs": ["Hello world here."],
              "real_indices": [0], "real_paras": ["Hello world here."]}]
    with pytest.raises(RuntimeError):
        tp.translate_reply_batch(batch)


# --- translate_replies (orchestrator) ---

def test_translate_replies_bounded_and_per_reply_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(tp, "translate_paragraphs",
                        lambda paras: [f"번역:{p}" for p in paras])
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
    def boom(paras):
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

    def boom(paras):
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
                        lambda paras: [f"번역:{p}" for p in paras])
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["translate_posts.py"])
    assert tp.main() == 0
    assert "reply:111:222" in saved
    assert saved["reply:111:222"]["status"] == "ok"
