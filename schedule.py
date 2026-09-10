"""모집공고 접수기간 파싱 및 마감 리마인더.

엘리스 모집공고는 제목에 접수기간이 거의 항상 포함되어 있다.
  [사송 롯데캐슬] 재임대 모집공고(접수기간 : 26.09.09~26.09.13)
  [하단 롯데캐슬]재임대 모집공고(모집기간:26.09.08~26.09.10)
  [어바니엘 천호] 공실세대 모집공고 (접수기간: 9/4~9/6)
이 모듈은 그 기간을 추출해 진행 중인 모집을 추적하고,
접수 시작일 / 마감 전날 / 마감 당일에 리마인더를 보낼지 판단한다.
"""

import json
import re
from datetime import date, datetime, timedelta

# "접수기간", "모집 기간", "신청기간", "접수일정" 뒤의 날짜 구간
PERIOD_RE = re.compile(r"(?:접수|모집|신청)\s*(?:기간|일정)\s*[:：]?\s*([^)\]\n]{3,60})")
# "접수일 '26.09.02" 처럼 단일 날짜만 있는 경우
SINGLE_DAY_RE = re.compile(r"(?:접수|신청)\s*일자?\s*[:：]?\s*([^)\]\n]{3,30})")
# YY.MM.DD / YYYY.MM.DD / M/D / MM-DD 등
DATE_RE = re.compile(r"(?:(\d{2,4})\s*[./-]\s*)?(\d{1,2})\s*[./-]\s*(\d{1,2})")
# 제목에 마감 표시가 붙는 경우: "_마감되었습니다", "----마감"
CLOSED_RE = re.compile(r"마감\s*(?:되었|됐|완료|입니다|\.|$|되)")

MAX_PERIOD_DAYS = 60


def parse_post_date(raw):
    """게시글 작성일('2026.09.09' 또는 '2026-09-09')을 date로 변환한다."""
    if not raw:
        return None
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(raw))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _resolve(year, month, day, ref):
    """(연, 월, 일) 토큰을 실제 date로 변환한다. 연도가 없으면 기준일로 추론."""
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None, False

    explicit = year is not None
    if explicit:
        year = int(year)
        if year < 100:
            year += 2000
        elif year < 1000:  # "251" 같은 오타 → 신뢰 불가
            return None, False
    else:
        year = ref.year

    try:
        resolved = date(year, month, day)
    except ValueError:
        return None, False

    if not explicit:
        # 연말/연초 경계 보정 (12월 공고의 1월 접수 등)
        if (ref - resolved).days > 30:
            try:
                resolved = date(year + 1, month, day)
            except ValueError:
                return None, False
        elif (resolved - ref).days > 300:
            try:
                resolved = date(year - 1, month, day)
            except ValueError:
                return None, False

    return resolved, explicit


def _dates_in(text, ref):
    """텍스트에서 날짜 토큰을 순서대로 추출한다."""
    found = []
    for m in DATE_RE.finditer(text):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        resolved, explicit = _resolve(y, mo, d, ref)
        if resolved:
            found.append((resolved, explicit))
    return found


def parse_period(title, post_date, content=None):
    """제목(우선) 또는 본문에서 접수기간을 추출한다.

    Returns:
        dict: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} 또는 None
    """
    ref = post_date if isinstance(post_date, date) else parse_post_date(post_date)
    if ref is None:
        return None

    for text in (title or "", content or ""):
        if not text:
            continue
        for regex in (PERIOD_RE, SINGLE_DAY_RE):
            for m in regex.finditer(text):
                dates = _dates_in(m.group(1), ref)
                if not dates:
                    continue
                start, _ = dates[0]
                if len(dates) >= 2:
                    end, end_explicit = dates[1]
                    if end < start and not end_explicit:
                        try:
                            end = date(end.year + 1, end.month, end.day)
                        except ValueError:
                            continue
                else:
                    end = start
                if end < start or (end - start).days > MAX_PERIOD_DAYS:
                    continue
                return {"start": start.isoformat(), "end": end.isoformat()}
    return None


def is_closed(title):
    """제목에 '마감' 표시가 붙었는지 확인한다."""
    return bool(CLOSED_RE.search(title or ""))


def empty_schedule():
    return {"entries": [], "meta": {"updated_at": None}}


def update_schedule(schedule, posts, today=None, excluded=None):
    """크롤링한 글에서 접수기간을 추출해 스케줄에 반영한다.

    Args:
        excluded: 제외할 단지 키워드 목록 (제목에 포함되면 건너뛴다)

    Returns:
        list: 새로 추가된 항목
    """
    today = today or date.today()
    excluded = excluded or []
    before = json.dumps(schedule["entries"], ensure_ascii=False, sort_keys=True)
    by_id = {e["id"]: e for e in schedule["entries"]}
    added = []

    for post in posts:
        title = post.get("title", "")
        if any(ex in title for ex in excluded):
            continue
        period = parse_period(title, post.get("date"), post.get("content_text") or post.get("content"))
        if not period:
            continue

        entry = by_id.get(post["id"])
        if entry is None:
            entry = {
                "id": post["id"],
                "title": title,
                "post_date": post.get("date"),
                "detail_url": post.get("detail_url"),
                "start": period["start"],
                "end": period["end"],
                "closed": is_closed(title),
                "reminded": [],
            }
            schedule["entries"].append(entry)
            by_id[post["id"]] = entry
            added.append(entry)
        else:
            # 공고가 정정되면 기간/마감 표시가 바뀔 수 있다
            entry["title"] = title
            entry["start"] = period["start"]
            entry["end"] = period["end"]
            entry["closed"] = entry.get("closed") or is_closed(title)
            entry.setdefault("reminded", [])
            entry.setdefault("detail_url", post.get("detail_url"))

    prune_schedule(schedule, today)

    # 실제로 바뀐 게 있을 때만 갱신 시각을 찍는다 (불필요한 커밋 방지)
    if json.dumps(schedule["entries"], ensure_ascii=False, sort_keys=True) != before:
        schedule["meta"]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return added


def prune_schedule(schedule, today=None, keep_days=90):
    """마감된 지 오래된 항목을 정리한다."""
    today = today or date.today()
    cutoff = today - timedelta(days=keep_days)
    schedule["entries"] = [
        e for e in schedule["entries"]
        if (parse_post_date(e.get("end")) or today) >= cutoff
    ]
    schedule["entries"].sort(key=lambda e: (e.get("end") or "", e.get("start") or ""))
    return schedule


def due_reminders(schedule, today=None):
    """오늘 보내야 할 리마인더 목록을 반환한다.

    Returns:
        list[(entry, kind, message)] — kind는 open/d1/d0
    """
    today = today or date.today()
    due = []

    for entry in schedule["entries"]:
        if entry.get("closed"):
            continue
        start = parse_post_date(entry.get("start"))
        end = parse_post_date(entry.get("end"))
        if not start or not end:
            continue
        sent = entry.setdefault("reminded", [])

        # 접수 시작 (공고일보다 늦게 시작하는 경우만 — 당일 시작은 신규 알림으로 갈음)
        post_date = parse_post_date(entry.get("post_date"))
        if start == today and (post_date is None or post_date < start) and "open" not in sent:
            due.append((entry, "open", "오늘 접수 시작"))

        if end == today + timedelta(days=1) and "d1" not in sent:
            due.append((entry, "d1", "내일 접수 마감"))

        if end == today and "d0" not in sent:
            due.append((entry, "d0", "오늘 접수 마감"))

    return due


def format_reminder(entry, kind, label):
    """리마인더 카카오톡 메시지 본문을 만든다."""
    icon = {"open": "[접수 시작]", "d1": "[마감 D-1]", "d0": "[마감 D-DAY]"}[kind]
    lines = [
        f"{icon} {label}",
        "",
        entry["title"],
        f"접수기간: {entry['start']} ~ {entry['end']}",
    ]
    if entry.get("detail_url"):
        lines += ["", entry["detail_url"]]
    return "\n".join(lines)


def dday(entry, today=None):
    """마감까지 남은 일수. 마감 후면 음수."""
    today = today or date.today()
    end = parse_post_date(entry.get("end"))
    if not end:
        return None
    return (end - today).days


def backfill(page_limit=None, excluded=None):
    """전체 게시글을 훑어 docs/schedule.json을 다시 만든다 (알림 없음)."""
    import os

    import requests

    from crawler import BASE_URL, fetch_recruit_list, parse_html_content, parse_title

    posts = []
    page = 1
    while True:
        items, pagination = fetch_recruit_list(page=page, page_size=20)
        if not items:
            break
        for item in items:
            nt_idx = item.get("nt_idx", "")
            posts.append({
                "id": item.get("nt_idx2"),
                "title": parse_title(item.get("nt_title", "")),
                "date": item.get("row_input_date"),
                "content": parse_html_content(item.get("nt_content", "")),
                "detail_url": (
                    f"{BASE_URL}/post/recruit/detail?i_sNtCode=BHCT"
                    f"&nt_idx={requests.utils.quote(nt_idx)}"
                ),
            })
        total_pages = int(pagination.get("i_iTotalPageCnt", 0))
        if page >= total_pages or (page_limit and page >= page_limit):
            break
        page += 1

    sched = empty_schedule()
    added = update_schedule(sched, posts, today=date.today(), excluded=excluded)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "schedule.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(sched, f, ensure_ascii=False, indent=2)

    # 백필로 만든 항목은 과거 공고이므로 리마인더를 소급 발송하지 않는다
    print(f"게시글 {len(posts)}건 중 접수기간 {len(added)}건 추출 → {out}")
    return sched


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from main import EXCLUDED_COMPLEXES

    sched = backfill(excluded=EXCLUDED_COMPLEXES)
    today = date.today().isoformat()
    live = [e for e in sched["entries"] if not e.get("closed") and e["end"] >= today]
    print(f"진행/예정 중인 모집 {len(live)}건")
    for e in live:
        print(f"  D{dday(e):+d}  {e['start']}~{e['end']}  {e['title'][:60]}")
