# -*- coding: utf-8 -*-
"""경쟁률 분석 로직 테스트."""
import pytest

import analyzer

TABLE_TEXT = """▣'26.04.10(금) 16시 접수 마감 집계
타입 | 모집세대수 | 접수자수 | 당첨자수 | 예비자수
-----+------------+----------+----------+---------
59A  | 3          | 249      | 3        | 6
84A  | 3          | 123      | 3        | 6
계   | 6          | 372      | 6        | 12
※ 당첨자 및 예비당첨자는 개별통보 됩니다."""


def test_parse_competition_table():
    rows = analyzer.parse_competition_table(TABLE_TEXT)
    assert [r["type"] for r in rows] == ["59A", "84A"]  # '계' 행은 제외
    assert rows[0] == {
        "type": "59A", "units": 3, "applicants": 249,
        "winners": 3, "reserves": 6, "rate": 83.0,
    }


def test_parse_competition_table_without_pipes():
    text = "타입 모집세대 접수건수\n59A 2 100\n84A 1 30"
    rows = analyzer.parse_competition_table(text)
    assert [(r["type"], r["rate"]) for r in rows] == [("59A", 50.0), ("84A", 30.0)]


def test_parse_competition_table_returns_none_without_header():
    assert analyzer.parse_competition_table("첨부파일을 참고하세요.") is None


def test_parse_competition_table_skips_malformed_rows():
    text = "타입 | 모집세대 | 접수건수\n59A | 3 | 249\n84A | 미정 | 미정\n"
    rows = analyzer.parse_competition_table(text)
    assert len(rows) == 1 and rows[0]["type"] == "59A"


@pytest.mark.parametrize("title,expected", [
    ("[사송 롯데캐슬] 재임대 모집공고(접수기간 : 26.09.09~26.09.13)", "사송 롯데캐슬"),
    ("[용산 남영역 롯데캐슬 헤리티지] 모집공고", "남영역 롯데캐슬"),   # 별칭 정규화
    ("[어바니엘 염창] 공실세대 모집공고", None),                      # 제외 단지
    ("[용산 원효 루미니] 모집공고", None),                            # 별칭 정규화 후 제외
    ("단지명 없는 제목", None),
])
def test_extract_complex_name(title, expected):
    assert analyzer.extract_complex_name(title) == expected


@pytest.mark.parametrize("title,expected", [
    ("[문래] '26.09.04 재임대 모집공고 접수 현황", "status"),
    ("[하단] 재임대 모집공고(모집기간:26.09.08~26.09.10)", "recruit"),
    ("[신동탄] 공실세대 임대차계약신청 안내", "notice"),
    ("무관한 글", "other"),
])
def test_classify_post(title, expected):
    assert analyzer.classify_post(title) == expected


def test_match_recruit_to_status_pairs_latest_prior_recruit():
    posts = [
        {"id": "1", "title": "[사송] 재임대 모집공고", "date": "2026.01.01", "content": ""},
        {"id": "2", "title": "[사송] 재임대 모집공고", "date": "2026.03.01", "content": ""},
        {"id": "3", "title": "[사송] 재임대 모집공고 접수 현황", "date": "2026.03.10",
         "content": TABLE_TEXT},
    ]
    matched = analyzer.match_recruit_to_status(posts)
    assert len(matched) == 1
    assert matched[0]["complex"] == "사송"
    assert matched[0]["recruit_id"] == "2"   # 현황 직전 공고와 매칭
    assert len(matched[0]["competition"]) == 2


def test_match_recruit_to_status_ignores_status_without_table():
    posts = [
        {"id": "1", "title": "[사송] 재임대 모집공고 접수 현황", "date": "2026.03.10",
         "content": "첨부 이미지 참고"},
    ]
    assert analyzer.match_recruit_to_status(posts) == []


def test_win_probability_certain_when_undersubscribed():
    assert analyzer.win_probability(0.8)["total"] == 1.0


def test_win_probability_decreases_with_rate():
    low = analyzer.win_probability(10)["total"]
    high = analyzer.win_probability(100)["total"]
    assert 0 < high < low < 1


def test_win_probability_reserve_component():
    """예비번호가 직접 당첨분에 확률을 더한다."""
    p = analyzer.win_probability(50, reserve_multiplier=3, reserve_conversion=0.3)
    assert p["direct"] == pytest.approx(0.02, abs=1e-6)
    assert p["reserve"] > 0
    assert p["total"] == pytest.approx(p["direct"] + p["reserve"], abs=1e-6)


def test_win_probability_zero_conversion_means_direct_only():
    p = analyzer.win_probability(50, reserve_conversion=0)
    assert p["reserve"] == 0
    assert p["total"] == p["direct"]


def _history(rates):
    return [{"date": f"2026.01.{i + 1:02d}", "rate": r} for i, r in enumerate(rates)]


def test_predict_rate_single_observation_is_low_confidence():
    p = analyzer.predict_rate(_history([40]))
    assert p["predicted"] == 40 and p["n_data"] == 1
    assert p["confidence"] == "low"
    assert p["low"] < 40 < p["high"]


def test_predict_rate_weights_recent_observations():
    """alpha가 클수록 최근 값에 가깝게 예측한다."""
    hist = _history([10, 10, 10, 100])
    recent_heavy = analyzer.predict_rate(hist, alpha=0.9)["predicted"]
    flat = analyzer.predict_rate(hist, alpha=0.1)["predicted"]
    assert recent_heavy > flat


def test_predict_rate_trend_adjustment():
    hist = _history([50, 50, 50])
    up = analyzer.predict_rate(hist, trend="up")["predicted"]
    stable = analyzer.predict_rate(hist, trend="stable")["predicted"]
    down = analyzer.predict_rate(hist, trend="down")["predicted"]
    assert down < stable < up


def test_predict_rate_confidence_grows_with_sample_size():
    assert analyzer.predict_rate(_history([30, 30]))["confidence"] == "low"
    assert analyzer.predict_rate(_history([30] * 3))["confidence"] == "medium"
    assert analyzer.predict_rate(_history([30] * 5))["confidence"] == "high"


def test_predict_rate_empty_history():
    assert analyzer.predict_rate([]) is None


def test_merge_archive_deduplicates_by_status_id():
    archive = analyzer.load_archive.__wrapped__() if hasattr(analyzer.load_archive, "__wrapped__") \
        else {"matches": [], "meta": {"created_at": None, "updated_at": None, "total_entries": 0}}
    match = {
        "complex": "사송", "status_id": "1", "status_date": "2026.03.10",
        "status_title": "현황", "recruit_date": "2026.03.01", "recruit_title": "공고",
        "competition": [{"type": "59A", "units": 1, "applicants": 10, "rate": 10.0}],
    }
    assert analyzer.merge_archive(archive, [match]) == 1
    assert analyzer.merge_archive(archive, [match]) == 0
    assert archive["meta"]["total_entries"] == 1
