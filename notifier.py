import json
import requests

MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def send_kakao_message(text, access_token, link_url="https://www.elyes.co.kr/post/recruit"):
    """카카오톡 '나에게 보내기'로 메시지를 전송한다."""
    # 카카오 텍스트 메시지 최대 길이
    if len(text) > 1000:
        text = text[:995] + "\n..."

    template = {
        "object_type": "text",
        "text": text,
        "link": {
            "web_url": link_url,
            "mobile_web_url": link_url,
        },
        "button_title": "모집공고 보기",
    }

    resp = requests.post(MEMO_URL, headers={
        "Authorization": f"Bearer {access_token}",
    }, data={
        "template_object": json.dumps(template),
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()


def format_post_message(post):
    """게시글을 카카오톡 메시지 형식으로 변환한다."""
    title = post["title"]
    date = post["date"]
    content = post["content_text"]

    # 본문이 길면 요약
    if len(content) > 400:
        content = content[:400] + "\n... (이하 생략)"

    msg = (
        f"[Elyes 모집공고]\n"
        f"{title}\n"
        f"작성일: {date}\n"
        f"─────────────\n"
        f"{content}"
    )
    return msg
