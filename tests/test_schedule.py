# -*- coding: utf-8 -*-
"""접수기간 파싱 및 마감 리마인더 테스트."""
from datetime import date

import pytest

import schedule as sched


# 실제 엘리스 공고 제목에서 수집한 표기 패턴
@pytest.mark.parametrize("title,post_date,expected", [
    # YY.MM.DD 범위
    ("[사송 롯데캐슬] 재임대 모집공고(접수기간 : 26.09.09~26.09.13)",
     "2026.09.09", ("2026-09-09", "2026-09-13")),
    # "모집기간" 표기
    ("[하단 롯데캐슬]재임대 모집공고(모집기간:26.09.08~26.09.10)",
     "2026.09.07", ("2026-09-08", "2026-09-10")),
    # 연도 생략 (M/D)
    ("[어바니엘 천호] 공실세대 모집공고 (접수기간: 9/4~9/6)",
     "2026.09.04", ("2026-09-04", "2026-09-06")),
    # 연도 생략 + 공백 + 0 패딩
    ("[어바니엘 한강] 공실세대 모집공고 (접수기간: 9/04 ~ 9/06)",
     "2026.09.04", ("2026-09-04", "2026-09-06")),
    # 4자리 연도
    ("[하단 롯데캐슬]재임대 모집공고문(모집기간 : 2026.08.08~2026.08.10)",
     "2026.08.07", ("2026-08-08", "2026-08-10")),
    # 슬래시 구분 YY/MM/DD
    ("[하단 롯데캐슬]재임대 모집공고(접수기간:25/09/15~25/09/21)--------마감",
     "2025.09.12", ("2025-09-15", "2025-09-21")),
    # 종료일 오타(2511.13) — 연도 없이 11/13으로 복구되어야 한다
    ("[하단 롯데캐슬]재임대 모집공고(접수기간 : 25.11.07~2511.13)",
     "2025.11.07", ("2025-11-07", "2025-11-13")),
    # 월 경계를 넘는 기간
    ("[어바니엘 천호] 공실세대 모집공고 (접수기간: 10/31~11/04)",
     "2025.10.31", ("2025-10-31", "2025-11-04")),
    # 공고일과 모집기간이 함께 있는 경우 — 모집기간을 써야 한다
    ("[하단 롯데캐슬]재임대 모집공고(공고일:25.09.23)(모집기간:25.09.23~25.09.28)---------------마감",
     "2025.09.23", ("2025-09-23", "2025-09-28")),
    # 단일 접수일
    ("[용산원효루미니] 추가모집공고(접수일 '26.09.02)-->마감되었습니다.",
     "2026.08.28", ("2026-09-02", "2026-09-02")),
    # 접수일시 + 시간대 표기 (시간은 날짜로 오인하지 않아야 한다)
    ("[독산역 롯데캐슬] 59타입 재임대 모집공고(접수일시: '26. 9.11(금) 10시~15시)",
     "2026.09.10", ("2026-09-11", "2026-09-11")),
])
def test_parse_period_real_titles(title, post_date, expected):
    result = sched.parse_period(title, post_date)
    assert result == {"start": expected[0], "end": expected[1]}


def test_parse_period_year_rollover():
    """12월 공고의 1월 접수는 다음 해로 해석한다."""
    result = sched.parse_period("[하단] 모집공고(접수기간: 12/29~1/5)", "2025.12.28")
    assert result == {"start": "2025-12-29", "end": "2026-01-05"}


def test_parse_period_without_period_keyword():
    """공고일만 있는 글은 기간을 만들어내지 않는다."""
    assert sched.parse_period(
        "[남영역 롯데캐슬] 공실세대 모집공고 (공고일:'26.09.04)", "2026.09.04") is None


def test_parse_period_falls_back_to_content():
    result = sched.parse_period(
        "[문래 롯데캐슬] 재임대 모집공고", "2026.09.04",
        content="자세한 사항은 첨부파일 참고\n접수기간: 26.09.05~26.09.08",
    )
    assert result == {"start": "2026-09-05", "end": "2026-09-08"}


def test_parse_period_rejects_absurd_range():
    """60일을 넘는 구간은 오파싱으로 보고 버린다."""
    assert sched.parse_period("모집공고(접수기간: 1/1~12/31)", "2026.01.01") is None


def test_parse_period_requires_post_date():
    assert sched.parse_period("모집공고(접수기간: 9/1~9/3)", None) is None


@pytest.mark.parametrize("title,expected", [
    ("[사송] 모집공고(접수기간 : 26.08.12~26.08.16) - 마감되었습니다.", True),
    ("[하단] 모집공고(모집기간:25.10.02~25.10.12)-------------마감", True),
    ("[한강] 재임대 모집공고 안내(접수기간:26.02.10~26.02.18)_마감되었습니다,", True),
    ("[하단 롯데캐슬]재임대 모집공고(모집기간:26.09.08~26.09.10)", False),
])
def test_is_closed(title, expected):
    assert sched.is_closed(title) is expected


def _post(pid, title, post_date, url="https://example.com/1"):
    return {"id": pid, "title": title, "date": post_date, "detail_url": url}


def test_update_schedule_adds_and_updates():
    s = sched.empty_schedule()
    posts = [_post("1", "[하단] 모집공고(접수기간: 26.09.08~26.09.10)", "2026.09.07")]

    added = sched.update_schedule(s, posts, today=date(2026, 9, 7))
    assert len(added) == 1
    assert s["entries"][0]["end"] == "2026-09-10"

    # 같은 글이 다시 크롤링돼도 중복 추가되지 않는다
    again = sched.update_schedule(s, posts, today=date(2026, 9, 8))
    assert again == []
    assert len(s["entries"]) == 1

    # 공고 정정으로 기간이 바뀌면 반영된다
    posts[0]["title"] = "[하단] 모집공고(접수기간: 26.09.08~26.09.11) 정정"
    sched.update_schedule(s, posts, today=date(2026, 9, 8))
    assert s["entries"][0]["end"] == "2026-09-11"


def test_update_schedule_skips_excluded_complex():
    s = sched.empty_schedule()
    posts = [
        _post("1", "[어바니엘 염창] 공실세대 모집공고 (접수기간: 9/4~9/6)", "2026.09.04"),
        _post("2", "[사송 롯데캐슬] 재임대 모집공고(접수기간 : 26.09.09~26.09.13)", "2026.09.09"),
    ]
    added = sched.update_schedule(s, posts, today=date(2026, 9, 9), excluded=["어바니엘"])
    assert [e["id"] for e in added] == ["2"]
    assert [e["id"] for e in s["entries"]] == ["2"]


def test_update_schedule_without_exclusions_keeps_everything():
    s = sched.empty_schedule()
    posts = [_post("1", "[어바니엘 염창] 공실세대 모집공고 (접수기간: 9/4~9/6)", "2026.09.04")]
    assert len(sched.update_schedule(s, posts, today=date(2026, 9, 4))) == 1


def test_due_reminders_lifecycle():
    s = sched.empty_schedule()
    posts = [_post("1", "[하단] 모집공고(접수기간: 26.09.08~26.09.10)", "2026.09.07")]
    sched.update_schedule(s, posts, today=date(2026, 9, 7))

    # 공고일에는 리마인더 없음 (신규 알림으로 갈음)
    assert sched.due_reminders(s, today=date(2026, 9, 7)) == []

    # 접수 시작일
    due = sched.due_reminders(s, today=date(2026, 9, 8))
    assert [k for _, k, _ in due] == ["open"]
    s["entries"][0]["reminded"].append("open")

    # 같은 날 다시 돌려도 중복 발송하지 않는다
    assert sched.due_reminders(s, today=date(2026, 9, 8)) == []

    # 마감 전날 → D-1
    due = sched.due_reminders(s, today=date(2026, 9, 9))
    assert [k for _, k, _ in due] == ["d1"]
    s["entries"][0]["reminded"].append("d1")

    # 마감 당일 → D-0
    due = sched.due_reminders(s, today=date(2026, 9, 10))
    assert [k for _, k, _ in due] == ["d0"]
    s["entries"][0]["reminded"].append("d0")

    # 마감 후에는 없음
    assert sched.due_reminders(s, today=date(2026, 9, 11)) == []


def test_due_reminders_skips_closed_posts():
    s = sched.empty_schedule()
    posts = [_post("1", "[하단] 모집공고(접수기간: 26.09.08~26.09.10) 마감되었습니다.", "2026.09.07")]
    sched.update_schedule(s, posts, today=date(2026, 9, 7))
    assert sched.due_reminders(s, today=date(2026, 9, 9)) == []


def test_due_reminders_same_day_start_has_no_open_reminder():
    """공고 당일 접수 시작이면 시작 리마인더는 보내지 않는다."""
    s = sched.empty_schedule()
    posts = [_post("1", "[사송] 모집공고(접수기간 : 26.09.09~26.09.13)", "2026.09.09")]
    sched.update_schedule(s, posts, today=date(2026, 9, 9))
    assert sched.due_reminders(s, today=date(2026, 9, 9)) == []


def test_prune_schedule_drops_old_entries():
    s = sched.empty_schedule()
    s["entries"] = [
        {"id": "old", "end": "2026-01-01", "reminded": []},
        {"id": "new", "end": "2026-09-10", "reminded": []},
    ]
    sched.prune_schedule(s, today=date(2026, 9, 10), keep_days=90)
    assert [e["id"] for e in s["entries"]] == ["new"]


def test_format_reminder_contains_key_facts():
    entry = {
        "title": "[하단] 모집공고",
        "start": "2026-09-08",
        "end": "2026-09-10",
        "detail_url": "https://www.elyes.co.kr/x",
    }
    msg = sched.format_reminder(entry, "d1", "내일 접수 마감")
    assert "D-1" in msg
    assert "[하단] 모집공고" in msg
    assert "2026-09-08 ~ 2026-09-10" in msg
    assert "https://www.elyes.co.kr/x" in msg


def test_dday():
    entry = {"end": "2026-09-10"}
    assert sched.dday(entry, today=date(2026, 9, 8)) == 2
    assert sched.dday(entry, today=date(2026, 9, 11)) == -1
