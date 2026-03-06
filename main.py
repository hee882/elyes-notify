import json
import os
import sys
import time

from dotenv import load_dotenv

from crawler import get_latest_posts
from kakao_auth import refresh_access_token
from notifier import send_kakao_message, format_post_message

load_dotenv()

SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen_posts.json")


def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_ids), f)


def write_github_output(key, value):
    """GitHub Actions output 변수를 설정한다."""
    output_file = os.getenv("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def check_and_notify():
    # 1) 카카오 토큰 갱신
    print("카카오 토큰 갱신 중...")
    token_data = refresh_access_token()
    access_token = token_data["access_token"]
    print("  access_token 갱신 완료")

    # refresh_token이 새로 발급되었으면 GitHub Actions에 알림
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
    new_posts = [p for p in posts if p["id"] not in seen_ids]

    if not new_posts:
        print("  새 글 없음")
        return

    # 오래된 글부터 전송
    new_posts.reverse()
    print(f"  새 글 {len(new_posts)}건 발견!")

    # 3) 카카오톡 전송
    for post in new_posts:
        try:
            msg = format_post_message(post)
            send_kakao_message(msg, access_token)
            seen_ids.add(post["id"])
            print(f"  전송 완료: {post['title']}")
            time.sleep(1)
        except Exception as e:
            print(f"  전송 실패 [{post['title']}]: {e}")

    save_seen_ids(seen_ids)


def main():
    check_and_notify()


if __name__ == "__main__":
    main()
