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
import random
import re
import sys
from datetime import datetime, timezone, timedelta

from crawler import fetch_recruit_list, parse_title, parse_html_content

BASE_DIR = os.path.dirname(__file__)
ANALYSIS_FILE = os.path.join(BASE_DIR, "docs", "analysis.json")
ARCHIVE_FILE = os.path.join(BASE_DIR, "docs", "archive.json")
KST = timezone(timedelta(hours=9))

# 분석 제외 단지 (키워드 포함 시 제외)
EXCLUDED_COMPLEXES = ["어바니엘", "한강 롯데캐슬 22단지", "하단 롯데캐슬", "용산원효루미니"]

# 단지명 정규화 (동일 단지의 다른 표기 통합)
COMPLEX_ALIASES = {
    "용산 원효 루미니": "용산원효루미니",
    "용산 남영역 롯데캐슬 헤리티지": "남영역 롯데캐슬",
}


def normalize_complex(name):
    return COMPLEX_ALIASES.get(name, name)


def extract_complex_name(title):
    """제목에서 [단지명]을 추출한다. 제외 대상은 None 반환."""
    m = re.match(r"\[(.+?)\]", title)
    if m:
        name = normalize_complex(m.group(1))
        if any(ex in name for ex in EXCLUDED_COMPLEXES):
            return None
        return name
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


def load_archive():
    """아카이브(누적 경쟁률 데이터)를 불러온다."""
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"matches": [], "meta": {"created_at": None, "updated_at": None, "total_entries": 0}}


def merge_archive(archive, new_matches):
    """새 매칭 데이터를 아카이브에 병합한다 (status_id 기준 중복 제거).

    Returns:
        int: 새로 추가된 항목 수
    """
    existing_ids = {m["status_id"] for m in archive["matches"]}
    added = 0
    for m in new_matches:
        if m["status_id"] not in existing_ids:
            archive["matches"].append({
                "complex": m["complex"],
                "status_id": m["status_id"],
                "status_date": m["status_date"],
                "status_title": m["status_title"],
                "recruit_date": m.get("recruit_date"),
                "recruit_title": m.get("recruit_title"),
                "competition": m["competition"],
            })
            existing_ids.add(m["status_id"])
            added += 1
    archive["meta"]["total_entries"] = len(archive["matches"])
    archive["meta"]["updated_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    if not archive["meta"]["created_at"]:
        archive["meta"]["created_at"] = archive["meta"]["updated_at"]
    return added


def save_archive(archive):
    """아카이브를 저장한다."""
    os.makedirs(os.path.dirname(ARCHIVE_FILE), exist_ok=True)
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)


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
        total_applicants = sum(c["applicants"] for c in comp)

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
                "applicants": c["applicants"],
                "co_types": co_types,
                "total_types_in_round": total_types,
                "total_units_in_round": total_units,
                "total_applicants_in_round": total_applicants,
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

            # 수요 분산 컨텍스트
            solo_ctxs = [c for c in t["contexts"] if c["total_types_in_round"] == 1]
            multi_ctxs = [c for c in t["contexts"] if c["total_types_in_round"] > 1]

            type_analysis[tname] = {
                "appearances": t["appearances"],
                "avg_rate": avg_rate,
                "weighted_rate": weighted_rate,
                "recent_avg": recent_avg,
                "trend": trend,
                "demand_context": {
                    "solo_avg_rate": round(sum(c["rate"] for c in solo_ctxs) / len(solo_ctxs), 1) if solo_ctxs else None,
                    "multi_avg_rate": round(sum(c["rate"] for c in multi_ctxs) / len(multi_ctxs), 1) if multi_ctxs else None,
                    "solo_count": len(solo_ctxs),
                    "multi_count": len(multi_ctxs),
                },
                "history": [
                    {
                        "date": ctx["date"],
                        "rate": ctx["rate"],
                        "units": ctx["units"],
                        "applicants": ctx["applicants"],
                        "co_types": ctx["co_types"],
                        "total_types_in_round": ctx["total_types_in_round"],
                        "total_units_in_round": ctx["total_units_in_round"],
                        "total_applicants_in_round": ctx["total_applicants_in_round"],
                    }
                    for ctx in sorted(t["contexts"], key=lambda x: x["date"])
                ],
            }

        # 단지 내 추천 타입 (가중 경쟁률 최저)
        recommended = min(type_analysis.items(), key=lambda x: x[1]["weighted_rate"])[0] if type_analysis else None

        result[cname] = {
            "total_rounds": stats["rounds"],
            "recommended_type": recommended,
            "types": type_analysis,
        }

    return result


def generate_insights(analysis):
    """분석 결과에서 핵심 인사이트를 도출한다.

    단지 내 타입 비교 중심:
    - 단지별 추천 타입 (가장 낮은 경쟁률)
    - 추세 감지 (급상승/하락)
    - 타입 조합 효과 (동시 모집 시 경쟁 분산)
    """
    insights = []

    all_histories = []

    # 단지별 추천 타입
    for cname, cdata in analysis.items():
        types = []
        for tname, tdata in cdata["types"].items():
            types.append({
                "type": tname,
                "weighted_rate": tdata["weighted_rate"],
                "avg_rate": tdata["avg_rate"],
                "recent_avg": tdata["recent_avg"],
                "trend": tdata["trend"],
                "appearances": tdata["appearances"],
            })
            for h in tdata.get("history", []):
                all_histories.append({
                    "complex": cname,
                    "type": tname,
                    "date": h["date"],
                    "rate": h["rate"],
                    "units": h["units"],
                    "co_types": h["co_types"],
                })

        if len(types) < 2:
            continue

        types.sort(key=lambda x: x["weighted_rate"])
        best = types[0]
        worst = types[-1]

        insights.append({
            "type": "opportunity",
            "title": f"{cname} 추천 타입",
            "message": f"{best['type']}타입 — 경쟁률 {best['weighted_rate']}:1 (최근 {best['recent_avg']}:1)",
            "detail": f"같은 단지 내 {worst['type']}타입({worst['weighted_rate']}:1) 대비 {round(worst['weighted_rate'] - best['weighted_rate'], 1)} 낮음",
        })

        # 추세 경고
        rising = [t for t in types if t["trend"] == "up" and t["appearances"] >= 2]
        if rising:
            r = rising[0]
            insights.append({
                "type": "warning",
                "title": f"{cname} {r['type']} 경쟁률 상승",
                "message": f"최근 {r['recent_avg']}:1 (평균 {r['avg_rate']}:1)",
                "detail": "최근 모집에서 경쟁이 증가하고 있습니다.",
            })

    # 타입 조합 효과 (전체 데이터 기반)
    _add_combination_insights(insights, all_histories)

    return insights


def _add_combination_insights(insights, histories):
    """동시 모집 타입 조합이 경쟁률에 미치는 영향을 분석한다."""
    if len(histories) < 4:
        return

    # 단독 모집 vs 복수 모집 비교
    solo = [h for h in histories if len(h["co_types"]) == 0]
    multi = [h for h in histories if len(h["co_types"]) > 0]

    if solo and multi:
        solo_avg = round(sum(h["rate"] for h in solo) / len(solo), 1)
        multi_avg = round(sum(h["rate"] for h in multi) / len(multi), 1)

        if solo_avg > multi_avg * 1.2:
            diff = round(solo_avg - multi_avg, 1)
            insights.append({
                "type": "info",
                "title": "타입 조합 효과",
                "message": f"단독 모집 시 평균 {solo_avg}:1 vs 복수 모집 시 {multi_avg}:1 (차이 {diff})",
                "detail": "여러 타입이 동시에 나올 때 경쟁이 분산됩니다. 복수 타입 모집 공고를 노리는 것이 유리합니다.",
            })

    # 많은 타입이 나올수록 경쟁률 낮아지는 패턴
    by_co_count = {}
    for h in histories:
        n = len(h["co_types"]) + 1  # 본인 포함 총 타입 수
        by_co_count.setdefault(n, []).append(h["rate"])

    if len(by_co_count) >= 2:
        sorted_counts = sorted(by_co_count.items())
        if len(sorted_counts) >= 2:
            first_avg = sum(sorted_counts[0][1]) / len(sorted_counts[0][1])
            last_avg = sum(sorted_counts[-1][1]) / len(sorted_counts[-1][1])
            if first_avg < last_avg * 0.7 and sorted_counts[-1][0] >= 3:
                insights.append({
                    "type": "trend",
                    "title": "타입 수와 경쟁률 관계",
                    "message": f"{sorted_counts[-1][0]}개 타입 동시 모집 시 평균 {round(last_avg, 1)}:1 → 경쟁 치열",
                    "detail": f"반면 {sorted_counts[0][0]}개 타입만 나올 때는 {round(first_avg, 1)}:1로 상대적으로 낮습니다.",
                })




# ====== 당첨 확률 최적화 ======

def predict_rate(history, trend="stable", alpha=0.4):
    """경쟁률을 지수가중이동평균(EWMA)으로 예측한다.

    Args:
        history: 과거 이력 [{date, rate, ...}, ...]
        trend: "up" / "down" / "stable"
        alpha: EWMA 감쇠율 (0~1, 클수록 최근 가중)

    Returns:
        dict: predicted, low, high (95% CI), std, confidence, n_data
    """
    if not history:
        return None

    sorted_h = sorted(history, key=lambda x: x["date"])
    rates = [h["rate"] for h in sorted_h]
    n = len(rates)

    if n == 1:
        r = rates[0]
        return {
            "predicted": r,
            "low": round(max(1, r * 0.5), 1),
            "high": round(r * 2.0, 1),
            "std": round(r * 0.5, 1),
            "confidence": "low",
            "n_data": 1,
        }
    weights = [(1 - alpha) ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)
    ewa = sum(r * w for r, w in zip(rates, weights)) / w_sum

    # 가중 표준편차 (최소 10% 불확실성)
    var = sum(w * (r - ewa) ** 2 for r, w in zip(rates, weights)) / w_sum
    std = max(var ** 0.5, ewa * 0.1)

    # 추세 반영
    predicted = ewa
    if trend == "up":
        predicted *= 1.1
    elif trend == "down":
        predicted *= 0.9

    # 소표본 보정 신뢰구간 (n이 작을수록 더 넓게)
    z = 1.96 * (1 + 3.0 / n)
    low = max(1, predicted - z * std)
    high = predicted + z * std

    return {
        "predicted": round(predicted, 1),
        "low": round(low, 1),
        "high": round(high, 1),
        "std": round(std, 1),
        "confidence": "high" if n >= 5 else ("medium" if n >= 3 else "low"),
        "n_data": n,
    }


def predict_rate_contextual(history, trend="stable", alpha=0.4):
    """수요 분산을 반영한 경쟁률 예측.

    단지 총수요(진입 수요)를 기준으로 정규화한 뒤 EWMA 예측하고,
    타입별 수요 분배 비율을 적용하여 최종 경쟁률을 산출한다.

    - solo_equiv_rate = 단지 전체 지원자 / 이 타입 세대수
      (이 타입만 있었다면 받았을 경쟁률)
    - demand_factor = 실제 경쟁률 / solo_equiv_rate
      (수요가 다른 타입으로 분산된 비율, <1이면 분산 효과)
    """
    if not history:
        return None

    sorted_h = sorted(history, key=lambda x: x["date"])
    n = len(sorted_h)

    # 컨텍스트 데이터 없으면 기본 예측으로 fallback
    has_context = all(
        h.get("total_applicants_in_round") is not None
        for h in sorted_h
    )
    if not has_context:
        return predict_rate(history, trend, alpha)

    if n == 1:
        h = sorted_h[0]
        r = h["rate"]
        se = h["total_applicants_in_round"] / h["units"] if h["units"] > 0 else r
        df = r / se if se > 0 else 1.0
        return {
            "predicted": r,
            "low": round(max(1, r * 0.5), 1),
            "high": round(r * 2.0, 1),
            "std": round(r * 0.5, 1),
            "confidence": "low",
            "n_data": 1,
            "demand_base": round(se, 1),
            "demand_factor": round(df, 3),
        }

    # 1. Solo equivalent rate (진입 수요 기준)
    solo_equivs = []
    dist_factors = []
    for h in sorted_h:
        se = h["total_applicants_in_round"] / h["units"] if h["units"] > 0 else h["rate"]
        solo_equivs.append(se)
        df = h["rate"] / se if se > 0 else 1.0
        dist_factors.append(df)

    # EWMA 가중치
    weights = [(1 - alpha) ** (n - 1 - i) for i in range(n)]
    w_sum = sum(weights)

    # 2. 진입 수요(solo equiv) EWMA
    ewa_solo = sum(r * w for r, w in zip(solo_equivs, weights)) / w_sum

    # 3. 수요 분산 계수 EWMA
    ewa_dist = sum(d * w for d, w in zip(dist_factors, weights)) / w_sum

    # 4. 최종 예측 = 진입 수요 × 분산 계수
    predicted = ewa_solo * ewa_dist

    if trend == "up":
        predicted *= 1.1
    elif trend == "down":
        predicted *= 0.9

    # 표준편차 (실제 rate 기준)
    rates = [h["rate"] for h in sorted_h]
    ewa_rate = sum(r * w for r, w in zip(rates, weights)) / w_sum
    var = sum(w * (r - ewa_rate) ** 2 for r, w in zip(rates, weights)) / w_sum
    std = max(var ** 0.5, predicted * 0.1)

    z = 1.96 * (1 + 3.0 / n)
    low = max(1, predicted - z * std)
    high = predicted + z * std

    return {
        "predicted": round(predicted, 1),
        "low": round(low, 1),
        "high": round(high, 1),
        "std": round(std, 1),
        "confidence": "high" if n >= 5 else ("medium" if n >= 3 else "low"),
        "n_data": n,
        "demand_base": round(ewa_solo, 1),
        "demand_factor": round(ewa_dist, 3),
    }


def win_probability(rate, reserve_multiplier=3, reserve_conversion=0.3):
    """당첨 확률을 계산한다 (직접 당첨 + 예비번호).

    Args:
        rate: 경쟁률 (지원자/세대)
        reserve_multiplier: 예비번호 배수 (기본 3배수)
        reserve_conversion: 예비번호 실제 전환율 (기본 30%)

    Returns:
        dict: direct, reserve, total 확률
    """
    if rate <= 1:
        return {"direct": 1.0, "reserve": 0.0, "total": 1.0}

    p_direct = 1.0 / rate

    # 예비번호: 직접 당첨 못한 사람 중에서 reserve_multiplier 배수만큼 선발
    # P(예비 선발 | 미당첨) = min(1, reserve_multiplier / (rate - 1))
    p_reserve_selected = min(1.0, reserve_multiplier / (rate - 1))
    p_reserve = (1 - p_direct) * p_reserve_selected * reserve_conversion

    return {
        "direct": round(p_direct, 6),
        "reserve": round(p_reserve, 6),
        "total": round(min(1.0, p_direct + p_reserve), 6),
    }


def generate_optimization(analysis, reserve_multiplier=3, reserve_conversion=0.3,
                          alpha=0.4):
    """당첨 확률 최적화 전략을 생성한다.

    - 타입별 예상 경쟁률 + 당첨 확률
    - 누적 당첨 확률 (연속 지원 시)
    - 몬테카를로 시뮬레이션
    - 최적 전략 추천
    """
    candidates = []

    for cname, cdata in analysis.items():
        for tname, tdata in cdata["types"].items():
            hist = tdata.get("history", [])
            trend = tdata.get("trend", "stable")
            pred = predict_rate_contextual(hist, trend, alpha=alpha)
            if not pred:
                continue

            prob = win_probability(pred["predicted"], reserve_multiplier, reserve_conversion)
            prob_worst = win_probability(pred["high"], reserve_multiplier, reserve_conversion)
            prob_best = win_probability(pred["low"], reserve_multiplier, reserve_conversion)

            # 수요 분산 컨텍스트
            dc = tdata.get("demand_context", {})

            candidates.append({
                "complex": cname,
                "type": tname,
                "predicted_rate": pred["predicted"],
                "rate_range": [pred["low"], pred["high"]],
                "rate_std": pred["std"],
                "confidence": pred["confidence"],
                "n_data": pred["n_data"],
                "trend": trend,
                "win_prob": round(prob["total"] * 100, 2),
                "win_prob_direct": round(prob["direct"] * 100, 2),
                "win_prob_reserve": round(prob["reserve"] * 100, 2),
                "win_prob_range": [
                    round(prob_worst["total"] * 100, 2),
                    round(prob_best["total"] * 100, 2),
                ],
                "demand_context": {
                    "demand_base": pred.get("demand_base"),
                    "demand_factor": pred.get("demand_factor"),
                    "solo_avg_rate": dc.get("solo_avg_rate"),
                    "multi_avg_rate": dc.get("multi_avg_rate"),
                    "solo_count": dc.get("solo_count", 0),
                    "multi_count": dc.get("multi_count", 0),
                },
            })

    candidates.sort(key=lambda x: x["win_prob"], reverse=True)

    # 최적 타입 연속 지원 시 누적 확률
    best_prob = candidates[0]["win_prob"] / 100 if candidates else 0
    repetition = []
    for k in range(1, 21):
        p_win_by_k = 1 - (1 - best_prob) ** k
        repetition.append({
            "rounds": k,
            "prob": round(p_win_by_k * 100, 2),
        })

    # 다양한 타입 순차 지원 시 누적 확률
    cumulative = []
    p_all_lose = 1.0
    for i, c in enumerate(candidates):
        p_all_lose *= (1 - c["win_prob"] / 100)
        cumulative.append({
            "round": i + 1,
            "label": f"{c['complex']} {c['type']}",
            "this_prob": c["win_prob"],
            "cumulative_prob": round((1 - p_all_lose) * 100, 2),
        })

    # 몬테카를로 시뮬레이션
    mc = _monte_carlo_rounds(
        candidates, n_sims=10000, max_rounds=30,
        reserve_multiplier=reserve_multiplier,
        reserve_conversion=reserve_conversion,
    )

    return {
        "params": {
            "reserve_multiplier": reserve_multiplier,
            "reserve_conversion": reserve_conversion,
        },
        "candidates": candidates,
        "repetition": repetition,
        "cumulative": cumulative,
        "monte_carlo": mc,
        "recommendation": _build_recommendation(candidates, repetition, mc),
    }


def _monte_carlo_rounds(candidates, n_sims=10000, max_rounds=30,
                         reserve_multiplier=3, reserve_conversion=0.3):
    """경쟁률 불확실성을 반영한 몬테카를로 시뮬레이션.

    매 라운드 최적 타입의 경쟁률을 삼각분포로 샘플링하여
    첫 당첨까지 걸리는 라운드 수를 시뮬레이션한다.
    """
    random.seed(42)

    if not candidates:
        return {"simulations": 0}

    best = candidates[0]
    low = max(1, best["rate_range"][0])
    high = max(best["rate_range"][1], best["predicted_rate"] + 1)
    pred = best["predicted_rate"]

    first_win_rounds = []

    for _ in range(n_sims):
        for k in range(1, max_rounds + 1):
            sampled_rate = random.triangular(low, high, pred)
            prob = win_probability(sampled_rate, reserve_multiplier, reserve_conversion)

            if random.random() < prob["total"]:
                first_win_rounds.append(k)
                break
        else:
            first_win_rounds.append(max_rounds + 1)

    # 통계
    wins_within = {}
    for target in [1, 3, 5, 10, 15, 20]:
        count = sum(1 for r in first_win_rounds if r <= target)
        wins_within[target] = round(count / n_sims * 100, 1)

    actual_wins = [r for r in first_win_rounds if r <= max_rounds]
    avg_rounds = round(sum(actual_wins) / max(len(actual_wins), 1), 1) if actual_wins else None
    median_rounds = sorted(actual_wins)[len(actual_wins) // 2] if actual_wins else None

    return {
        "simulations": n_sims,
        "max_rounds": max_rounds,
        "best_type": f"{best['complex']} {best['type']}",
        "win_rate": round(len(actual_wins) / n_sims * 100, 1),
        "wins_within": wins_within,
        "avg_rounds_to_win": avg_rounds,
        "median_rounds_to_win": median_rounds,
    }


def _build_recommendation(candidates, repetition, mc):
    """실행 가능한 전략 추천을 생성한다."""
    if not candidates:
        return {"summary": "데이터 부족"}

    best = candidates[0]

    # 50% 돌파 라운드
    rounds_to_50 = None
    for r in repetition:
        if r["prob"] >= 50:
            rounds_to_50 = r["rounds"]
            break

    # 80% 돌파 라운드
    rounds_to_80 = None
    for r in repetition:
        if r["prob"] >= 80:
            rounds_to_80 = r["rounds"]
            break

    return {
        "best_type": f"{best['complex']} {best['type']}",
        "best_rate": best["predicted_rate"],
        "best_prob": best["win_prob"],
        "best_prob_direct": best["win_prob_direct"],
        "best_prob_reserve": best["win_prob_reserve"],
        "rounds_to_50pct": rounds_to_50,
        "rounds_to_80pct": rounds_to_80,
        "mc_avg_rounds": mc.get("avg_rounds_to_win"),
        "mc_median_rounds": mc.get("median_rounds_to_win"),
    }


# ====== 백테스트 & 파라미터 튜닝 ======

def backtest(matched_data, alpha=0.4, reserve_multiplier=3, reserve_conversion=0.3,
             verbose=False):
    """Walk-forward 백테스트: 각 공고를 시간순으로 순회하며 예측 정확도를 검증한다.

    매 라운드 i에서:
    - rounds[0..i-1]의 데이터만으로 모델 구축
    - round[i]의 경쟁률을 예측
    - 예측 vs 실제 비교

    Returns:
        dict: predictions, mae, mape, ci_coverage, pick_accuracy
    """
    sorted_matches = sorted(matched_data, key=lambda x: x["status_date"])

    if len(sorted_matches) < 3:
        return {"error": "백테스트에 최소 3건 필요", "predictions": []}

    predictions = []

    for i in range(2, len(sorted_matches)):
        prior = sorted_matches[:i]
        current = sorted_matches[i]

        # 사전 데이터로 분석
        analysis = analyze_competition(prior)
        cname = current["complex"]
        if cname not in analysis:
            continue

        actual_types = {c["type"]: c for c in current["competition"]}
        cdata = analysis[cname]

        for tname, tdata in cdata["types"].items():
            if tname not in actual_types:
                continue

            hist = tdata.get("history", [])
            trend = tdata.get("trend", "stable")
            pred = predict_rate_contextual(hist, trend, alpha=alpha)
            if not pred:
                continue

            actual_rate = actual_types[tname]["rate"]
            predicted_rate = pred["predicted"]
            error = predicted_rate - actual_rate
            pct_error = (error / actual_rate * 100) if actual_rate > 0 else 0

            pred_prob = win_probability(predicted_rate, reserve_multiplier, reserve_conversion)
            actual_prob = win_probability(actual_rate, reserve_multiplier, reserve_conversion)

            predictions.append({
                "round": i + 1,
                "date": current["status_date"],
                "complex": cname,
                "type": tname,
                "predicted_rate": predicted_rate,
                "actual_rate": actual_rate,
                "error": round(error, 1),
                "pct_error": round(pct_error, 1),
                "abs_pct_error": round(abs(pct_error), 1),
                "pred_win_prob": round(pred_prob["total"] * 100, 2),
                "actual_win_prob": round(actual_prob["total"] * 100, 2),
                "within_ci": pred["low"] <= actual_rate <= pred["high"],
                "n_prior": pred["n_data"],
            })

    if not predictions:
        return {"error": "예측 가능한 데이터 없음", "predictions": []}

    # ── 집계 통계 ──
    mae = round(sum(abs(p["error"]) for p in predictions) / len(predictions), 1)
    mape = round(sum(p["abs_pct_error"] for p in predictions) / len(predictions), 1)
    ci_hits = sum(1 for p in predictions if p["within_ci"])
    ci_rate = round(ci_hits / len(predictions) * 100, 1)

    # 최적 타입 선택 정확도: 모델이 추천한 타입이 실제 최저 경쟁률이었나?
    round_groups = {}
    for p in predictions:
        key = (p["round"], p["complex"])
        round_groups.setdefault(key, []).append(p)

    correct_picks = 0
    total_testable = 0
    for preds in round_groups.values():
        if len(preds) < 2:
            continue
        total_testable += 1
        best_predicted = min(preds, key=lambda x: x["predicted_rate"])
        best_actual = min(preds, key=lambda x: x["actual_rate"])
        if best_predicted["type"] == best_actual["type"]:
            correct_picks += 1

    pick_accuracy = round(correct_picks / max(total_testable, 1) * 100, 1)

    result = {
        "total_predictions": len(predictions),
        "mae": mae,
        "mape": mape,
        "ci_coverage": ci_rate,
        "pick_accuracy": pick_accuracy,
        "testable_rounds": total_testable,
        "correct_picks": correct_picks,
        "predictions": predictions,
    }

    if verbose:
        print(f"\n{'='*65}")
        print(f" Walk-Forward 백테스트 (alpha={alpha})")
        print(f"{'='*65}")
        print(f"  총 예측: {len(predictions)}건 / 테스트 라운드: {total_testable}개")
        print(f"  MAE: {mae} | MAPE: {mape}%")
        print(f"  신뢰구간 적중: {ci_rate}% ({ci_hits}/{len(predictions)})")
        print(f"  최적 타입 선택: {pick_accuracy}% ({correct_picks}/{total_testable})")
        print(f"\n{'─'*65}")
        print(f" {'#':>3} {'날짜':>12} {'단지':>16} {'타입':>5} {'예측':>7} {'실제':>7} {'오차':>8} {'CI':>3}")
        print(f"{'─'*65}")
        for p in predictions:
            ci = "O" if p["within_ci"] else "X"
            print(f" {p['round']:>3} {p['date']:>12} {p['complex']:>16} "
                  f"{p['type']:>5} {p['predicted_rate']:>7.1f} {p['actual_rate']:>7.1f} "
                  f"{p['pct_error']:>+7.1f}% {ci:>3}")

    return result


def tune_model(matched_data, verbose=True):
    """백테스트 기반으로 EWMA alpha를 그리드 서치한다.

    alpha만 튜닝 대상 (경쟁률 예측에 직접 영향).
    reserve_conversion은 도메인 지식 기반 고정값 (예측 정확도와 무관).

    Returns:
        dict: best alpha, backtest metrics
    """
    alphas = [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7]

    best = None
    all_results = []

    for a in alphas:
        bt = backtest(matched_data, alpha=a, verbose=False)
        if "error" in bt:
            continue

        # 종합 점수: MAPE 낮을수록 + 선택 정확도 높을수록 + CI 적중률 높을수록
        score = bt["mape"] - bt["pick_accuracy"] * 0.5 - bt["ci_coverage"] * 0.3

        entry = {
            "alpha": a,
            "mape": bt["mape"],
            "mae": bt["mae"],
            "ci_coverage": bt["ci_coverage"],
            "pick_accuracy": bt["pick_accuracy"],
            "score": round(score, 2),
        }
        all_results.append(entry)

        if best is None or score < best["score"]:
            best = entry

    if verbose and all_results:
        print(f"\n{'='*60}")
        print(f" EWMA alpha 튜닝 (그리드 서치)")
        print(f"{'='*60}")
        all_results.sort(key=lambda x: x["score"])
        print(f" {'alpha':>6} {'MAPE':>7} {'MAE':>6} {'CI적중':>7} {'선택정확':>8} {'점수':>7}")
        print(f" {'─'*48}")
        for r in all_results:
            marker = " <-- BEST" if r is all_results[0] else ""
            print(f" {r['alpha']:>6.2f}"
                  f" {r['mape']:>6.1f}% {r['mae']:>6.1f}"
                  f" {r['ci_coverage']:>6.1f}% {r['pick_accuracy']:>7.1f}%"
                  f" {r['score']:>7.2f}{marker}")

        print(f"\n 최적 alpha = {best['alpha']} (기본값 0.4)")

    # reserve_conversion은 도메인 파라미터: 30% 기본값 유지
    if best:
        best["reserve_conversion"] = 0.3

    return best


def run_analysis():
    """분석을 실행하고 결과를 저장한다. 이전 결과가 있으면 증분 업데이트."""
    prev = load_previous_analysis()
    archive = load_archive()
    print(f"아카이브 로드: {archive['meta']['total_entries']}건 보존 중")

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

    # 아카이브에 새 데이터 병합
    archived_count = merge_archive(archive, matched)
    if archived_count:
        print(f"  아카이브에 {archived_count}건 새로 추가 (총 {archive['meta']['total_entries']}건)")
    save_archive(archive)

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
        # 재크롤링 결과도 아카이브에 병합
        extra = merge_archive(archive, matched)
        if extra:
            print(f"  아카이브에 {extra}건 추가 병합")
            save_archive(archive)
        new_matches = [m for m in matched if m["status_id"] not in prev_match_ids]
        print(f"  {len(matched)}건 매칭")

    # 버전 결정
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    if not new_matches and mode == "incremental" and prev:
        # 새 현황 데이터 없음 → 이전 분석 유지
        print("  새 현황 데이터 없음 → 이전 분석 유지")

        # known_post_ids가 변경된 경우에만 파일 저장 (불필요한 커밋 방지)
        if all_known_ids != known_ids:
            prev["meta"]["known_post_ids"] = sorted(all_known_ids)
            os.makedirs(os.path.dirname(ANALYSIS_FILE), exist_ok=True)
            with open(ANALYSIS_FILE, "w", encoding="utf-8") as f:
                json.dump(prev, f, ensure_ascii=False, indent=2)
            print(f"  게시글 ID 업데이트 ({len(all_known_ids)}건)")
        else:
            print(f"  변경사항 없음 — 저장 스킵")

        print(f"\n분석 유지 v{prev_version}")
        return prev

    if new_matches or mode == "full":
        new_version = prev_version + 1
        print(f"  새 현황 데이터 {len(new_matches)}건 발견 → v{new_version}")
    else:
        new_version = prev_version + 1

    # 아카이브 기반 분석 (웹에서 삭제된 데이터도 포함)
    all_matched = archive["matches"]
    print(f"경쟁률 분석 중... (아카이브 {len(all_matched)}건 기반)")
    analysis = analyze_competition(all_matched)

    print("인사이트 생성 중...")
    insights = generate_insights(analysis)

    # 백테스트 & 파라미터 튜닝
    print("\n백테스트 & 파라미터 튜닝 중...")
    tuned = tune_model(all_matched, verbose=True)
    best_alpha = tuned["alpha"] if tuned else 0.4
    best_rc = tuned["reserve_conversion"] if tuned else 0.3

    # 튜닝된 파라미터로 최종 백테스트 (상세 출력)
    bt_result = backtest(all_matched, alpha=best_alpha, reserve_conversion=best_rc, verbose=True)

    print(f"\n당첨 확률 최적화 중... (alpha={best_alpha}, rc={best_rc})")
    optimization = generate_optimization(
        analysis, alpha=best_alpha, reserve_conversion=best_rc,
    )

    # 백테스트 결과를 optimization에 포함
    optimization["backtest"] = {
        "mape": bt_result.get("mape"),
        "mae": bt_result.get("mae"),
        "ci_coverage": bt_result.get("ci_coverage"),
        "pick_accuracy": bt_result.get("pick_accuracy"),
        "total_predictions": bt_result.get("total_predictions"),
        "tuned_alpha": best_alpha,
        "tuned_reserve_conversion": best_rc,
    }
    optimization["params"]["alpha"] = best_alpha
    optimization["params"]["reserve_conversion"] = best_rc

    rec = optimization.get("recommendation", {})
    if rec.get("best_prob"):
        print(f"  최적 타입: {rec['best_type']} — 1회 {rec['best_prob']}%")
        if rec.get("rounds_to_50pct"):
            print(f"  50% 돌파: {rec['rounds_to_50pct']}회 연속 지원")
        mc = optimization.get("monte_carlo", {})
        if mc.get("avg_rounds_to_win"):
            print(f"  시뮬레이션 평균: {mc['avg_rounds_to_win']}회 만에 당첨")

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
        "optimization": optimization,
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

    # 커밋 메시지 생성
    _write_commit_message(result, new_version, new_matches, analysis,
                          optimization, bt_result, archive)

    return result


COMMIT_MSG_FILE = os.path.join(BASE_DIR, ".commit_msg")


def _write_commit_message(result, version, new_matches, analysis,
                          optimization, bt_result, archive):
    """분석 결과 기반 커밋 메시지를 파일로 생성한다."""
    lines = []

    # 제목
    if new_matches:
        complexes = list({m["complex"] for m in new_matches})
        if len(complexes) <= 2:
            title = f"v{version} 분석 갱신: {', '.join(complexes)}"
        else:
            title = f"v{version} 분석 갱신: {complexes[0]} 외 {len(complexes)-1}개 단지"
    else:
        title = f"v{version} 분석 재계산"
    lines.append(title)
    lines.append("")

    # 데이터 현황
    lines.append(f"[데이터] 아카이브 {archive['meta']['total_entries']}건 | 단지 {len(analysis)}개")
    if new_matches:
        for m in new_matches[:5]:
            types_str = ", ".join(
                f"{c['type']}({c['rate']}:1)" for c in m["competition"]
            )
            lines.append(f"  + {m['complex']} {m['status_date']} — {types_str}")

    # 단지별 경쟁률 요약
    lines.append("")
    lines.append("[경쟁률]")
    for cname, cdata in sorted(analysis.items()):
        types = " | ".join(
            f"{t} {d['avg_rate']}:1({'↑' if d.get('trend') == 'up' else '↓' if d.get('trend') == 'down' else '→'})"
            for t, d in sorted(cdata["types"].items(), key=lambda x: x[1]["weighted_rate"])
        )
        lines.append(f"  {cname} ({cdata['total_rounds']}회): {types}")

    # 모델 파라미터
    params = optimization.get("params", {})
    bt = optimization.get("backtest", {})
    lines.append("")
    lines.append(f"[모델] alpha={params.get('alpha', '?')} | "
                 f"MAPE {bt.get('mape', '?')}% | "
                 f"적중 {bt.get('pick_accuracy', '?')}% | "
                 f"CI {bt.get('ci_coverage', '?')}%")

    # 추천
    rec = optimization.get("recommendation", {})
    if rec.get("best_type"):
        lines.append("")
        lines.append(f"[추천] {rec['best_type']} — "
                     f"1회 {rec.get('best_prob', '?')}% "
                     f"(직접 {rec.get('best_prob_direct', '?')}% + "
                     f"예비 {rec.get('best_prob_reserve', '?')}%)")
        if rec.get("rounds_to_50pct"):
            mc = optimization.get("monte_carlo", {})
            lines.append(f"  50%: {rec['rounds_to_50pct']}회 | "
                         f"80%: {rec.get('rounds_to_80pct', '-')}회 | "
                         f"MC평균: {mc.get('avg_rounds_to_win', '?')}회")

    msg = "\n".join(lines)
    with open(COMMIT_MSG_FILE, "w", encoding="utf-8") as f:
        f.write(msg)
    print(f"\n커밋 메시지 생성 → {COMMIT_MSG_FILE}")



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
