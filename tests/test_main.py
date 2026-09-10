# -*- coding: utf-8 -*-
"""알림 파이프라인 테스트 (카카오 API는 모두 모킹)."""
import json
from datetime import date

import pytest

import main


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    """모든 상태 파일을 임시 디렉터리로 돌린다."""
    monkeypatch.setattr(main, "SEEN_FILE", str(tmp_path / "seen_posts.json"))
    monkeypatch.setattr(main, "FAILED_FILE", str(tmp_path / "failed_posts.json"))
    monkeypatch.setattr(main, "HISTORY_FILE", str(tmp_path / "docs" / "history.json"))
    monkeypatch.setattr(main, "SCHEDULE_FILE", str(tmp_path / "docs" / "schedule.json"))
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    return tmp_path


def test_load_seen_ids_reads_cache(workdir):
    main.save_json_file(main.SEEN_FILE, ["1", "2"])
    assert main.load_seen_ids() == {"1", "2"}


def test_load_seen_ids_recovers_from_history_when_cache_lost(workdir):
    """Actions 캐시가 날아가도 알림 이력으로 재발송을 막는다."""
    main.save_json_file(main.HISTORY_FILE, {
        "records": [
            {"id": "1", "status": "sent"},
            {"id": "2", "status": "retried"},
            {"id": "3", "status": "failed"},     # 실패 건은 재전송 대상이므로 제외
            {"status": "sent"},                  # id 없는 손상 레코드
        ],
        "stats": {},
    })
    assert main.load_seen_ids() == {"1", "2"}


def test_load_seen_ids_empty_when_nothing_known(workdir):
    assert main.load_seen_ids() == set()


class FakeKakao:
    def __init__(self):
        self.texts = []

    def __call__(self, text, access_token):
        self.texts.append(text)
        return {"result_code": 0}


def _post(pid, title, post_date="2026.09.07"):
    return {
        "id": pid, "title": title, "date": post_date,
        "content_text": "", "image_url": None,
        "detail_url": f"https://www.elyes.co.kr/x/{pid}",
    }


def test_send_deadline_reminders_creates_schedule_and_stays_quiet_on_announce_day(workdir, monkeypatch):
    kakao = FakeKakao()
    monkeypatch.setattr(main, "send_kakao_text", kakao)
    posts = [_post("1", "[사송 롯데캐슬] 재임대 모집공고(모집기간:26.09.08~26.09.10)")]

    changed = main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 7))

    assert changed is True
    assert kakao.texts == []          # 공고 당일에는 리마인더 없음
    saved = json.load(open(main.SCHEDULE_FILE, encoding="utf-8"))
    assert saved["entries"][0]["start"] == "2026-09-08"
    assert saved["entries"][0]["end"] == "2026-09-10"


def test_send_deadline_reminders_sends_d1_once(workdir, monkeypatch):
    kakao = FakeKakao()
    monkeypatch.setattr(main, "send_kakao_text", kakao)
    posts = [_post("1", "[사송 롯데캐슬] 재임대 모집공고(모집기간:26.09.08~26.09.10)")]

    main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 7))
    main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 9))
    assert len(kakao.texts) == 1
    assert "D-1" in kakao.texts[0]

    # 같은 날 재실행(하루 4회 크론)해도 중복 발송하지 않는다
    main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 9))
    assert len(kakao.texts) == 1


def test_send_deadline_reminders_marks_only_successful_sends(workdir, monkeypatch):
    def failing(text, token):
        raise RuntimeError("카카오 전송 실패 (401)")

    monkeypatch.setattr(main, "send_kakao_text", failing)
    posts = [_post("1", "[사송 롯데캐슬] 재임대 모집공고(모집기간:26.09.08~26.09.10)")]
    main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 7))
    main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 9))

    saved = json.load(open(main.SCHEDULE_FILE, encoding="utf-8"))
    assert saved["entries"][0]["reminded"] == []   # 실패했으므로 다음 실행에서 재시도

    kakao = FakeKakao()
    monkeypatch.setattr(main, "send_kakao_text", kakao)
    main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 9))
    assert len(kakao.texts) == 1


def test_send_deadline_reminders_reports_no_change(workdir, monkeypatch):
    monkeypatch.setattr(main, "send_kakao_text", FakeKakao())
    posts = [_post("1", "[남영역 롯데캐슬] 공실세대 모집공고 (공고일:'26.09.04)")]
    # 기간 정보가 없는 글만 있으면 스케줄 파일을 건드리지 않는다
    assert main.send_deadline_reminders(posts, ["token"], today=date(2026, 9, 7)) is False
