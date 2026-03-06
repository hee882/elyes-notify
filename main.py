import json
import os
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from crawler import get_latest_posts
from kakao_auth import refresh_access_token
from notifier import send_kakao_message

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
SEEN_FILE = os.path.join(BASE_DIR, "seen_posts.json")
HISTORY_FILE = os.path.join(BASE_DIR, "docs", "history.json")
KST = timezone(timedelta(hours=9))


def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f)


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"records": [], "stats": {"total_sent": 0, "last_run": None}}


def save_history(history):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def write_github_output(key, value):
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def check_and_notify():
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    # 1) 카카오 토큰 갱신
    print("카카오 토큰 갱신 중...")
    token_data = refresh_access_token()
    access_token = token_data["access_token"]
    print("  access_token 갱신 완료")

    if token_data["new_refresh_token"]:
        print("  refresh_token도 갱신됨 → GitHub Secret 업데이트 예정")
        write_github_output("new_refresh_token", token_data["new_refresh_token"])

    # 2) 모집공고 크롤링
    print("모집공고 확인 중...")
    try:
        posts = get_latest_posts(count=10)
    except Exception as e:
        print(f"  크롤링 실패: {e}")
        return

    seen_ids = load_seen_ids()
    history = load_history()

    new_posts = [p for p in posts if p["id"] not in seen_ids]

    # 히스토리 업데이트 (실행 기록)
    history["stats"]["last_run"] = now_kst

    if not new_posts:
        print("  새 글 없음")
        save_history(history)
        return

    # 오래된 글부터 전송
    new_posts.reverse()
    print(f"  새 글 {len(new_posts)}건 발견!")

    # 3) 카카오톡 전송
    for post in new_posts:
        record = {
            "id": post["id"],
            "title": post["title"],
            "date": post["date"],
            "notified_at": now_kst,
            "status": "pending",
            "has_image": bool(post.get("image_url")),
        }
        try:
            send_kakao_message(post, access_token)
            seen_ids.add(post["id"])
            record["status"] = "sent"
            history["stats"]["total_sent"] += 1
            print(f"  전송 완료: {post['title']}")
            time.sleep(1)
        except Exception as e:
            record["status"] = "failed"
            record["error"] = str(e)
            print(f"  전송 실패 [{post['title']}]: {e}")

        history["records"].insert(0, record)

    # 최근 200건만 유지
    history["records"] = history["records"][:200]

    save_seen_ids(seen_ids)
    save_history(history)


def main():
    check_and_notify()


if __name__ == "__main__":
    main()
