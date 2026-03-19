import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from crawler import get_latest_posts
from kakao_auth import refresh_access_token
from notifier import send_kakao_message, send_kakao_text

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
SEEN_FILE = os.path.join(BASE_DIR, "seen_posts.json")
FAILED_FILE = os.path.join(BASE_DIR, "failed_posts.json")
HISTORY_FILE = os.path.join(BASE_DIR, "docs", "history.json")
KST = timezone(timedelta(hours=9))

# 알림 제외 단지
EXCLUDED_COMPLEXES = ["어바니엘", "한강 롯데캐슬 22단지", "하단 롯데캐슬", "용산원효루미니"]


def load_json_file(path, default=None):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def save_json_file(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_seen_ids():
    return set(load_json_file(SEEN_FILE, []))


def save_seen_ids(seen_ids):
    save_json_file(SEEN_FILE, list(seen_ids))


def load_history():
    return load_json_file(HISTORY_FILE, {
        "records": [],
        "stats": {"total_sent": 0, "total_failed": 0, "total_retried": 0, "last_run": None},
    })


def load_failed_posts():
    """전송 실패한 글 목록을 불러온다."""
    return load_json_file(FAILED_FILE, [])


def save_failed_posts(posts):
    save_json_file(FAILED_FILE, posts)


def write_github_output(key, value):
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def retry_failed_posts(access_token, friend_access_token, history, now_kst):
    """이전에 실패한 글을 재전송한다."""
    failed = load_failed_posts()
    if not failed:
        return

    print(f"\n  미전송 글 {len(failed)}건 재전송 시도...")

    # 재전송 안내 메시지
    titles = "\n".join(f"  - {p['title']}" for p in failed)
    msg = (
        f"[Elyes 알림 복구]\n"
        f"이전에 전송 실패한 글 {len(failed)}건을 재전송합니다.\n\n"
        f"{titles}"
    )
    
    # 1) 본인에게 안내
    if access_token:
        send_kakao_text(msg, access_token)
    
    # 2) 친구에게 안내 (있을 경우만)
    if friend_access_token:
        try:
            send_kakao_text(msg, friend_access_token)
        except Exception as e:
            print(f"  친구 재전송 안내 실패: {e}")

    time.sleep(1)

    seen_ids = load_seen_ids()
    still_failed = []

    for post in failed:
        record = {
            "id": post["id"],
            "title": post["title"],
            "date": post["date"],
            "notified_at": now_kst,
            "status": "pending",
            "has_image": post.get("has_image", False),
            "detail_url": post.get("detail_url"),
            "retry": True,
        }
        try:
            # 본인에게 전송
            if access_token:
                send_kakao_message(post, access_token)
            
            # 친구에게 전송 (있을 경우만)
            if friend_access_token:
                try:
                    send_kakao_message(post, friend_access_token)
                except Exception as fe:
                    print(f"  친구에게 재전송 실패: {fe}")

            seen_ids.add(post["id"])
            record["status"] = "retried"
            history["stats"]["total_retried"] += 1
            history["stats"]["total_sent"] += 1
            print(f"  재전송 완료: {post['title']}")
            time.sleep(1)
        except Exception as e:
            record["status"] = "failed"
            record["error"] = str(e)
            still_failed.append(post)
            print(f"  재전송 실패 [{post['title']}]: {e}")

        history["records"].insert(0, record)

    save_seen_ids(seen_ids)
    save_failed_posts(still_failed)


def check_and_notify():
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    # 1) 카카오 토큰 갱신 (본인)
    print("본인 카카오 토큰 갱신 중...")
    access_token = None
    try:
        token_data = refresh_access_token()
        access_token = token_data["access_token"]
        if token_data["new_refresh_token"]:
            write_github_output("new_refresh_token", token_data["new_refresh_token"])
        print("  본인 access_token 갱신 완료")
    except Exception as e:
        print(f"  본인 토큰 갱신 실패: {e}")

    # 2) 카카오 토큰 갱신 (친구 - 있을 경우만)
    friend_access_token = None
    friend_refresh_token = os.getenv("KAKAO_FRIEND_REFRESH_TOKEN")
    if friend_refresh_token:
        print("친구 카카오 토큰 갱신 중...")
        try:
            f_token_data = refresh_access_token(refresh_token=friend_refresh_token)
            friend_access_token = f_token_data["access_token"]
            if f_token_data["new_refresh_token"]:
                write_github_output("new_friend_refresh_token", f_token_data["new_refresh_token"])
            print("  친구 access_token 갱신 완료")
        except Exception as e:
            print(f"  친구 토큰 갱신 실패: {e}")

    if not access_token and not friend_access_token:
        print("전송 가능한 토큰이 없습니다. 종료합니다.")
        return

    history = load_history()
    history["stats"]["last_run"] = now_kst
    
    # 3) 이전 실패 글 재전송
    retry_failed_posts(access_token, friend_access_token, history, now_kst)

    # 4) 모집공고 크롤링
    print("\n모집공고 확인 중...")
    try:
        posts = get_latest_posts(count=10)
    except Exception as e:
        print(f"  크롤링 실패: {e}")
        save_json_file(HISTORY_FILE, history)
        return

    seen_ids = load_seen_ids()
    new_posts = [
        p for p in posts 
        if p["id"] not in seen_ids and not any(ex in p["title"] for ex in EXCLUDED_COMPLEXES)
    ]

    if not new_posts:
        print("  새 글 없음")
        save_json_file(HISTORY_FILE, history)
        return

    new_posts.reverse()
    print(f"  새 글 {len(new_posts)}건 발견!")

    # 5) 카카오톡 전송
    failed_posts = load_failed_posts()

    for post in new_posts:
        record = {
            "id": post["id"],
            "title": post["title"],
            "date": post["date"],
            "notified_at": now_kst,
            "status": "pending",
            "has_image": bool(post.get("image_url")),
            "detail_url": post.get("detail_url"),
        }
        try:
            # 본인에게 전송
            if access_token:
                send_kakao_message(post, access_token)
            
            # 친구에게 전송 (있을 경우만)
            if friend_access_token:
                try:
                    send_kakao_message(post, friend_access_token)
                except Exception as fe:
                    print(f"  친구 전송 실패 [{post['title']}]: {fe}")

            seen_ids.add(post["id"])
            record["status"] = "sent"
            history["stats"]["total_sent"] += 1
            print(f"  전송 완료: {post['title']}")
            time.sleep(1)
        except Exception as e:
            record["status"] = "failed"
            record["error"] = str(e)
            history["stats"]["total_failed"] += 1
            # 실패한 글 저장 (다음 실행 시 재전송)
            failed_posts.append({
                "id": post["id"],
                "title": post["title"],
                "date": post["date"],
                "content_text": post["content_text"],
                "image_url": post.get("image_url"),
                "detail_url": post.get("detail_url"),
                "has_image": bool(post.get("image_url")),
            })
            print(f"  전송 실패 [{post['title']}]: {e}")

        history["records"].insert(0, record)

    history["records"] = history["records"][:500]

    save_seen_ids(seen_ids)
    save_failed_posts(failed_posts)
    save_json_file(HISTORY_FILE, history)


def main():
    check_and_notify()


if __name__ == "__main__":
    main()
