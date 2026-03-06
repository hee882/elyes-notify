"""엘리스 모집공고 경쟁률 분석 모듈.

전체 과거 데이터를 크롤링하여 단지별/타입별 경쟁률을 분석하고,
결과를 JSON으로 저장한다. 증분 업데이트를 지원한다.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

from crawler import fetch_recruit_list, parse_title, parse_html_content

BASE_DIR = os.path.dirname(__file__)
ANALYSIS_FILE = os.path.join(BASE_DIR, "dashboard", "analysis.json")
KST = timezone(timedelta(hours=9))

# 단지명 정규화 (동일 단지의 다른 표기 통합)
COMPLEX_ALIASES = {
    "용산 원효 루미니": "용산원효루미니",
    "용산 남영역 롯데캐슬 헤리티지": "남영역 롯데캐슬",
}


def normalize_complex(name):
    return COMPLEX_ALIASES.get(name, name)


def extract_complex_name(title):
    """제목에서 [단지명]을 추출한다."""
    m = re.match(r"\[(.+?)\]", title)
    if m:
        return normalize_complex(m.group(1))
    return None


def classify_post(title):
    """게시글 유형을 분류한다."""
    if "현황" in title:
        return "status"  # 접수 현황 (결과)
    elif "모집공고" in title or "모집 공고" in title:
        return "recruit"  # 모집공고
    elif "안내" in title:
        return "notice"  # 접수 안내
    return "other"


def parse_competition_table(content):
    """현황 게시글 본문에서 경쟁률 테이블을 파싱한다.

    Returns:
        list[dict]: [{"type": "59A", "units": 5, "applicants": 409, "winners": 5, "reserves": 15}, ...]
        None: 테이블을 찾을 수 없는 경우
    """
    lines = content.strip().split("\n")
    results = []

    # 헤더 행 찾기: "타입" 이 포함된 행
    header_idx = None
    for i, line in enumerate(lines):
        if "타입" in line and ("모집" in line or "접수" in line):
            header_idx = i
            break

    if header_idx is None:
        return None

    # 구분선 건너뛰기
    data_start = header_idx + 1
    if data_start < len(lines) and re.match(r"^[-+]+$", lines[data_start].strip()):
        data_start += 1

    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or line.startswith("※"):
            break

        # "|" 또는 공백으로 분리
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
        else:
            cells = line.split()

        if len(cells) < 3:
            continue

        type_name = cells[0]
        # "계" 행은 건너뛰기
        if type_name in ("계", "합계", "소계"):
            continue

        try:
            units = int(cells[1].replace(",", ""))
            applicants = int(cells[2].replace(",", ""))
        except (ValueError, IndexError):
            continue

        winners = 0
        reserves = 0
        if len(cells) >= 4:
            try:
                winners = int(cells[3].replace(",", ""))
            except ValueError:
                pass
        if len(cells) >= 5:
            try:
                reserves = int(cells[4].replace(",", ""))
            except ValueError:
                pass

        results.append({
            "type": type_name,
            "units": units,
            "applicants": applicants,
            "winners": winners,
            "reserves": reserves,
            "rate": round(applicants / units, 1) if units > 0 else 0,
        })

    return results if results else None


def crawl_all_posts():
    """엘리스 전체 모집공고를 크롤링한다."""
    sys.stdout.reconfigure(encoding="utf-8")
    all_posts = []
    page = 1

    while True:
        items, pagination = fetch_recruit_list(page=page, page_size=20)
        if not items:
            break

        for item in items:
            title = parse_title(item.get("nt_title", ""))
            content = parse_html_content(item.get("nt_content", ""))
            all_posts.append({
                "id": item.get("nt_idx2"),
                "title": title,
                "date": item.get("row_input_date"),
                "content": content,
            })

        total_pages = int(pagination.get("i_iTotalPageCnt", 0))
        if page >= total_pages:
            break
        page += 1

    return all_posts


def match_recruit_to_status(posts):
    """모집공고와 접수현황을 단지명 기반으로 매칭한다.

    같은 단지의 모집공고 → 현황 순서를 시간순으로 매칭.
    """
    by_complex = {}
    for p in posts:
        cname = extract_complex_name(p["title"])
        if not cname:
            continue
        ptype = classify_post(p["title"])
        if ptype not in ("recruit", "status"):
            continue
        by_complex.setdefault(cname, []).append({**p, "post_type": ptype})

    matched = []
    for cname, cposts in by_complex.items():
        # 날짜순 정렬
        cposts.sort(key=lambda x: x["date"])
        recruits = [p for p in cposts if p["post_type"] == "recruit"]
        statuses = [p for p in cposts if p["post_type"] == "status"]

        for status in statuses:
            competition = parse_competition_table(status["content"])
            if not competition:
                continue

            # 이 현황보다 이전 날짜의 가장 가까운 모집공고를 매칭
            matched_recruit = None
            for r in reversed(recruits):
                if r["date"] <= status["date"]:
                    matched_recruit = r
                    break

            matched.append({
                "complex": cname,
                "status_id": status["id"],
                "status_date": status["date"],
                "status_title": status["title"],
                "recruit_id": matched_recruit["id"] if matched_recruit else None,
                "recruit_date": matched_recruit["date"] if matched_recruit else None,
                "recruit_title": matched_recruit["title"] if matched_recruit else None,
                "competition": competition,
            })

    return matched


def analyze_competition(matched_data):
    """단지별/타입별 경쟁률을 분석한다.

    단순 평균이 아닌, 동시 모집 타입 수와 세대수를 가중치로 반영한다.
    - 같이 나온 타입이 많을수록 경쟁이 분산됨
    - 모집 세대수가 적을수록 경쟁률이 높아지는 경향
    """
    complex_stats = {}

    for entry in matched_data:
        cname = entry["complex"]
        comp = entry["competition"]
        total_types = len(comp)
        total_units = sum(c["units"] for c in comp)

        if cname not in complex_stats:
            complex_stats[cname] = {
                "rounds": 0,
                "types": {},
            }

        stats = complex_stats[cname]
        stats["rounds"] += 1

        for c in comp:
            tname = c["type"]
            if tname not in stats["types"]:
                stats["types"][tname] = {
                    "appearances": 0,
                    "total_units": 0,
                    "total_applicants": 0,
                    "total_winners": 0,
                    "rates": [],
                    "contexts": [],  # 동시 모집 상황
                }

            t = stats["types"][tname]
            t["appearances"] += 1
            t["total_units"] += c["units"]
            t["total_applicants"] += c["applicants"]
            t["total_winners"] += c["winners"]
            t["rates"].append(c["rate"])

            # 동시 모집 컨텍스트 (어떤 타입과 함께 나왔는지)
            co_types = [x["type"] for x in comp if x["type"] != tname]
            t["contexts"].append({
                "date": entry["status_date"],
                "rate": c["rate"],
                "units": c["units"],
                "co_types": co_types,
                "total_types_in_round": total_types,
                "total_units_in_round": total_units,
            })

    # 최종 지표 계산
    result = {}
    for cname, stats in complex_stats.items():
        type_analysis = {}
        for tname, t in stats["types"].items():
            avg_rate = round(sum(t["rates"]) / len(t["rates"]), 1) if t["rates"] else 0

            # 가중 경쟁률: 모집세대수 역비례 가중
            # 세대수가 적은 라운드의 경쟁률에 더 높은 가중치
            weighted_sum = 0
            weight_total = 0
            for ctx in t["contexts"]:
                # 가중치: 1/units * (1 + co_types 수 보정)
                # 동시 모집 타입이 많을수록 경쟁 분산 → 보정
                dispersion = 1 + (ctx["total_types_in_round"] - 1) * 0.15
                weight = dispersion / max(ctx["units"], 1)
                weighted_sum += ctx["rate"] * weight
                weight_total += weight

            weighted_rate = round(weighted_sum / weight_total, 1) if weight_total > 0 else 0

            # 최근 추세 (최근 3회 vs 전체 평균)
            recent_rates = [ctx["rate"] for ctx in sorted(t["contexts"], key=lambda x: x["date"])[-3:]]
            recent_avg = round(sum(recent_rates) / len(recent_rates), 1) if recent_rates else 0
            trend = "up" if recent_avg > avg_rate * 1.1 else ("down" if recent_avg < avg_rate * 0.9 else "stable")

            # 경쟁 난이도 점수 (1~10)
            difficulty = min(10, max(1, round(weighted_rate / 20)))

            type_analysis[tname] = {
                "appearances": t["appearances"],
                "avg_rate": avg_rate,
                "weighted_rate": weighted_rate,
                "recent_avg": recent_avg,
                "trend": trend,
                "difficulty": difficulty,
                "total_units": t["total_units"],
                "total_applicants": t["total_applicants"],
                "history": [
                    {
                        "date": ctx["date"],
                        "rate": ctx["rate"],
                        "units": ctx["units"],
                        "co_types": ctx["co_types"],
                    }
                    for ctx in sorted(t["contexts"], key=lambda x: x["date"])
                ],
            }

        result[cname] = {
            "total_rounds": stats["rounds"],
            "types": type_analysis,
        }

    return result


def run_analysis():
    """전체 분석을 실행하고 결과를 저장한다."""
    print("엘리스 전체 데이터 크롤링 중...")
    posts = crawl_all_posts()
    print(f"  총 {len(posts)}건 크롤링 완료")

    print("모집공고 ↔ 접수현황 매칭 중...")
    matched = match_recruit_to_status(posts)
    print(f"  {len(matched)}건 매칭 완료")

    print("경쟁률 분석 중...")
    analysis = analyze_competition(matched)

    # 날짜 범위 계산
    dates = [p["date"] for p in posts if p["date"]]
    date_range = {"from": min(dates), "to": max(dates)} if dates else {}

    result = {
        "meta": {
            "analyzed_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            "total_posts": len(posts),
            "matched_results": len(matched),
            "complexes": len(analysis),
            "date_range": date_range,
        },
        "complexes": analysis,
        "matches": [
            {
                "complex": m["complex"],
                "status_date": m["status_date"],
                "status_title": m["status_title"],
                "recruit_date": m["recruit_date"],
                "recruit_title": m["recruit_title"],
                "competition": m["competition"],
            }
            for m in matched
        ],
    }

    os.makedirs(os.path.dirname(ANALYSIS_FILE), exist_ok=True)
    with open(ANALYSIS_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n분석 완료! → {ANALYSIS_FILE}")
    print(f"  기간: {date_range.get('from', '?')} ~ {date_range.get('to', '?')}")
    print(f"  단지: {len(analysis)}개")
    for cname, data in sorted(analysis.items()):
        types = ", ".join(
            f"{t}({d['avg_rate']}:1)" for t, d in data["types"].items()
        )
        print(f"  [{cname}] {data['total_rounds']}회 | {types}")

    return result


if __name__ == "__main__":
    run_analysis()
