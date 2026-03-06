"""엘리스 모집공고 경쟁률 분석 모듈.

전체 과거 데이터를 크롤링하여 단지별/타입별 경쟁률을 분석하고,
결과를 JSON으로 저장한다. 증분 업데이트를 지원한다.

버전 관리:
  - 최초 실행 시 전체 크롤링 (full scan)
  - 이후 실행 시 새 게시글만 크롤링 (incremental)
  - 새 현황 데이터 발견 시 분석 재계산
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
        return "status"
    elif "모집공고" in title or "모집 공고" in title:
        return "recruit"
    elif "안내" in title:
        return "notice"
    return "other"


def parse_competition_table(content):
    """현황 게시글 본문에서 경쟁률 테이블을 파싱한다."""
    lines = content.strip().split("\n")
    results = []

    header_idx = None
    for i, line in enumerate(lines):
        if "타입" in line and ("모집" in line or "접수" in line):
            header_idx = i
            break

    if header_idx is None:
        return None

    data_start = header_idx + 1
    if data_start < len(lines) and re.match(r"^[-+]+$", lines[data_start].strip()):
        data_start += 1

    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or line.startswith("※"):
            break

        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
        else:
            cells = line.split()

        if len(cells) < 3:
            continue

        type_name = cells[0]
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


def load_previous_analysis():
    """이전 분석 결과를 불러온다."""
    if os.path.exists(ANALYSIS_FILE):
        with open(ANALYSIS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def crawl_all_posts(known_ids=None):
    """엘리스 전체 모집공고를 크롤링한다.

    known_ids가 주어지면 이미 알고있는 게시글은 건너뛰되,
    새 글이 없는 페이지를 만나면 조기 종료한다 (증분 모드).
    """
    sys.stdout.reconfigure(encoding="utf-8")
    all_posts = []
    page = 1
    new_count = 0
    stop_early = known_ids is not None

    while True:
        items, pagination = fetch_recruit_list(page=page, page_size=20)
        if not items:
            break

        page_has_new = False
        for item in items:
            post_id = item.get("nt_idx2")
            title = parse_title(item.get("nt_title", ""))
            content = parse_html_content(item.get("nt_content", ""))
            post = {
                "id": post_id,
                "title": title,
                "date": item.get("row_input_date"),
                "content": content,
            }
            all_posts.append(post)

            if known_ids and post_id not in known_ids:
                page_has_new = True
                new_count += 1

        # 증분 모드: 새 글이 없는 페이지가 나오면 이미 알고있는 데이터
        if stop_early and not page_has_new and page > 1:
            print(f"  페이지 {page}에서 새 글 없음 → 조기 종료")
            break

        total_pages = int(pagination.get("i_iTotalPageCnt", 0))
        if page >= total_pages:
            break
        page += 1

    if known_ids:
        print(f"  새 게시글: {new_count}건")
    return all_posts


def match_recruit_to_status(posts):
    """모집공고와 접수현황을 단지명 기반으로 매칭한다."""
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
        cposts.sort(key=lambda x: x["date"])
        recruits = [p for p in cposts if p["post_type"] == "recruit"]
        statuses = [p for p in cposts if p["post_type"] == "status"]

        for status in statuses:
            competition = parse_competition_table(status["content"])
            if not competition:
                continue

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
    """단지별/타입별 경쟁률을 분석한다."""
    complex_stats = {}

    for entry in matched_data:
        cname = entry["complex"]
        comp = entry["competition"]
        total_types = len(comp)
        total_units = sum(c["units"] for c in comp)

        if cname not in complex_stats:
            complex_stats[cname] = {"rounds": 0, "types": {}}

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
                    "contexts": [],
                }

            t = stats["types"][tname]
            t["appearances"] += 1
            t["total_units"] += c["units"]
            t["total_applicants"] += c["applicants"]
            t["total_winners"] += c["winners"]
            t["rates"].append(c["rate"])

            co_types = [x["type"] for x in comp if x["type"] != tname]
            t["contexts"].append({
                "date": entry["status_date"],
                "rate": c["rate"],
                "units": c["units"],
                "co_types": co_types,
                "total_types_in_round": total_types,
                "total_units_in_round": total_units,
            })

    result = {}
    for cname, stats in complex_stats.items():
        type_analysis = {}
        for tname, t in stats["types"].items():
            avg_rate = round(sum(t["rates"]) / len(t["rates"]), 1) if t["rates"] else 0

            weighted_sum = 0
            weight_total = 0
            for ctx in t["contexts"]:
                dispersion = 1 + (ctx["total_types_in_round"] - 1) * 0.15
                weight = dispersion / max(ctx["units"], 1)
                weighted_sum += ctx["rate"] * weight
                weight_total += weight

            weighted_rate = round(weighted_sum / weight_total, 1) if weight_total > 0 else 0

            recent_rates = [ctx["rate"] for ctx in sorted(t["contexts"], key=lambda x: x["date"])[-3:]]
            recent_avg = round(sum(recent_rates) / len(recent_rates), 1) if recent_rates else 0
            trend = "up" if recent_avg > avg_rate * 1.1 else ("down" if recent_avg < avg_rate * 0.9 else "stable")

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


def generate_insights(analysis):
    """분석 결과에서 핵심 인사이트를 도출한다.

    규칙 기반 분석으로, 각 단지/타입의 특성을 요약한다.
    """
    insights = []

    # 전체 타입 경쟁률 순위
    all_types = []
    for cname, cdata in analysis.items():
        for tname, tdata in cdata["types"].items():
            all_types.append({
                "complex": cname,
                "type": tname,
                "weighted_rate": tdata["weighted_rate"],
                "avg_rate": tdata["avg_rate"],
                "recent_avg": tdata["recent_avg"],
                "trend": tdata["trend"],
                "appearances": tdata["appearances"],
                "total_applicants": tdata["total_applicants"],
                "total_units": tdata["total_units"],
            })

    if not all_types:
        return insights

    all_types.sort(key=lambda x: x["weighted_rate"])

    # 가장 경쟁률 낮은 타입 (기회)
    best = all_types[0]
    insights.append({
        "type": "opportunity",
        "title": "가장 낮은 경쟁률",
        "message": f"{best['complex']} {best['type']}타입 — 가중 경쟁률 {best['weighted_rate']}:1",
        "detail": f"평균 {best['avg_rate']}:1, 최근 {best['recent_avg']}:1 (총 {best['appearances']}회 모집)",
    })

    # 가장 경쟁률 높은 타입 (주의)
    worst = all_types[-1]
    insights.append({
        "type": "warning",
        "title": "가장 높은 경쟁률",
        "message": f"{worst['complex']} {worst['type']}타입 — 가중 경쟁률 {worst['weighted_rate']}:1",
        "detail": f"평균 {worst['avg_rate']}:1, 최근 {worst['recent_avg']}:1 (총 {worst['appearances']}회 모집)",
    })

    # 급상승 추세 타입
    rising = [t for t in all_types if t["trend"] == "up" and t["appearances"] >= 2]
    if rising:
        rising.sort(key=lambda x: x["recent_avg"] - x["avg_rate"], reverse=True)
        r = rising[0]
        insights.append({
            "type": "trend",
            "title": "경쟁률 급상승",
            "message": f"{r['complex']} {r['type']}타입 — 최근 {r['recent_avg']}:1 (평균 대비 +{round(r['recent_avg'] - r['avg_rate'], 1)})",
            "detail": "최근 모집에서 경쟁이 크게 증가하고 있습니다.",
        })

    # 하락 추세 타입 (기회)
    falling = [t for t in all_types if t["trend"] == "down" and t["appearances"] >= 2]
    if falling:
        falling.sort(key=lambda x: x["avg_rate"] - x["recent_avg"], reverse=True)
        f = falling[0]
        insights.append({
            "type": "opportunity",
            "title": "경쟁률 하락 추세",
            "message": f"{f['complex']} {f['type']}타입 — 최근 {f['recent_avg']}:1 (평균 대비 -{round(f['avg_rate'] - f['recent_avg'], 1)})",
            "detail": "최근 모집에서 경쟁이 완화되고 있어 기회일 수 있습니다.",
        })

    # 데이터 충분한 단지 vs 부족한 단지
    for cname, cdata in analysis.items():
        if cdata["total_rounds"] >= 5:
            types_str = ", ".join(
                f"{t}({d['weighted_rate']}:1)"
                for t, d in sorted(cdata["types"].items(), key=lambda x: x[1]["weighted_rate"])
            )
            insights.append({
                "type": "info",
                "title": f"{cname} 종합",
                "message": f"{cdata['total_rounds']}회 모집 데이터 기반 — 신뢰도 높음",
                "detail": f"타입별: {types_str}",
            })

    return insights


def run_analysis():
    """분석을 실행하고 결과를 저장한다. 이전 결과가 있으면 증분 업데이트."""
    prev = load_previous_analysis()

    # 이전 분석의 게시글 ID 목록 추출
    known_ids = set()
    prev_version = 0
    if prev and "meta" in prev:
        known_ids = set(prev["meta"].get("known_post_ids", []))
        prev_version = prev["meta"].get("version", 0)
        print(f"이전 분석 v{prev_version} 로드 (게시글 {len(known_ids)}건 기록)")

    # 1단계: 새 글 확인 (빠른 증분 스캔)
    mode = "incremental" if known_ids else "full"
    print(f"엘리스 데이터 크롤링 중... ({mode})")
    posts = crawl_all_posts(known_ids if known_ids else None)
    print(f"  총 {len(posts)}건 수집")

    current_ids = {p["id"] for p in posts}
    new_ids = current_ids - known_ids
    all_known_ids = known_ids | current_ids

    print(f"모집공고 <-> 접수현황 매칭 중...")
    matched = match_recruit_to_status(posts)
    print(f"  {len(matched)}건 매칭 완료")

    # 새 현황 데이터가 있는지 확인
    prev_match_ids = set()
    if prev and "matches" in prev:
        prev_match_ids = {m.get("status_id") or m.get("status_date", "") for m in prev["matches"]}
    new_matches = [m for m in matched if m["status_id"] not in prev_match_ids]

    # 2단계: 새 현황 데이터가 발견되면 전체 재크롤링 (정확한 분석 위해)
    if new_matches and mode == "incremental":
        print(f"\n  새 현황 {len(new_matches)}건 → 전체 재크롤링...")
        mode = "full-rescan"
        posts = crawl_all_posts(None)
        print(f"  총 {len(posts)}건 수집 (전체)")
        all_known_ids = {p["id"] for p in posts} | known_ids
        matched = match_recruit_to_status(posts)
        new_matches = [m for m in matched if m["status_id"] not in prev_match_ids]
        print(f"  {len(matched)}건 매칭")

    # 버전 결정
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    if not new_matches and mode == "incremental" and prev:
        # 새 현황 데이터 없음 → 이전 분석 유지, 메타만 업데이트
        print("  새 현황 데이터 없음 → 이전 분석 유지")
        prev["meta"]["last_checked"] = now_kst
        prev["meta"]["known_post_ids"] = sorted(all_known_ids)

        os.makedirs(os.path.dirname(ANALYSIS_FILE), exist_ok=True)
        with open(ANALYSIS_FILE, "w", encoding="utf-8") as f:
            json.dump(prev, f, ensure_ascii=False, indent=2)

        print(f"\n분석 유지 v{prev_version} (마지막 확인: {now_kst})")
        return prev

    if new_matches or mode == "full":
        new_version = prev_version + 1
        print(f"  새 현황 데이터 {len(new_matches)}건 발견 → v{new_version}")
    else:
        new_version = prev_version + 1

    # full 모드이므로 전체 데이터로 분석
    print("경쟁률 분석 중...")
    analysis = analyze_competition(matched)

    print("인사이트 생성 중...")
    insights = generate_insights(analysis)

    dates = [p["date"] for p in posts if p["date"]]
    date_range = {"from": min(dates), "to": max(dates)} if dates else {}

    result = {
        "meta": {
            "version": new_version,
            "analyzed_at": now_kst,
            "last_checked": now_kst,
            "mode": mode,
            "total_posts": len(posts),
            "new_posts": len(new_ids),
            "matched_results": len(matched),
            "new_matches": len(new_matches),
            "complexes": len(analysis),
            "date_range": date_range,
            "known_post_ids": sorted(all_known_ids),
            "changelog": _build_changelog(prev, new_version, now_kst, new_ids, new_matches),
        },
        "insights": insights,
        "complexes": analysis,
        "matches": [
            {
                "complex": m["complex"],
                "status_id": m["status_id"],
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

    print(f"\n분석 완료! v{new_version} → {ANALYSIS_FILE}")
    print(f"  기간: {date_range.get('from', '?')} ~ {date_range.get('to', '?')}")
    print(f"  단지: {len(analysis)}개, 인사이트: {len(insights)}건")
    for cname, data in sorted(analysis.items()):
        types = ", ".join(
            f"{t}({d['avg_rate']}:1)" for t, d in data["types"].items()
        )
        print(f"  [{cname}] {data['total_rounds']}회 | {types}")

    return result


def _build_changelog(prev, new_version, now_kst, new_ids, new_matches):
    """변경 이력을 누적 관리한다."""
    changelog = []
    if prev and "meta" in prev:
        changelog = prev["meta"].get("changelog", [])

    if new_version > (prev["meta"].get("version", 0) if prev and "meta" in prev else 0):
        entry = {
            "version": new_version,
            "date": now_kst,
            "new_posts": len(new_ids),
            "new_matches": len(new_matches),
        }
        if new_matches:
            entry["new_match_summaries"] = [
                f"{m['complex']} ({m['status_date']})" for m in new_matches[:5]
            ]
        changelog.append(entry)

    # 최근 20개까지만 유지
    return changelog[-20:]


if __name__ == "__main__":
    run_analysis()
