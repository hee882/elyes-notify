import json
import requests

MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"


def send_kakao_message(post, access_token):
    """카카오톡 '나에게 보내기'로 feed 템플릿 메시지를 전송한다."""
    detail_url = post.get("detail_url", "https://www.elyes.co.kr/post/recruit")

    description = post["content_text"]
    if len(description) > 200:
        description = description[:200] + "..."

    content = {
        "title": post["title"],
        "description": description,
        "link": {
            "web_url": detail_url,
            "mobile_web_url": detail_url,
        },
    }

    # 이미지가 있으면 썸네일로 추가
    if post.get("image_url"):
        content["image_url"] = post["image_url"]
        content["image_width"] = 800
        content["image_height"] = 600

    template = {
        "object_type": "feed",
        "content": content,
        "item_content": {
            "items": [
                {"item": "작성일", "item_op": post["date"]},
            ],
        },
        "buttons": [
            {
                "title": "상세보기",
                "link": {
                    "web_url": detail_url,
                    "mobile_web_url": detail_url,
                },
            },
        ],
    }

    resp = requests.post(MEMO_URL, headers={
        "Authorization": f"Bearer {access_token}",
    }, data={
        "template_object": json.dumps(template),
    }, timeout=10)

    if resp.status_code != 200:
        raise RuntimeError(f"카카오 전송 실패 ({resp.status_code}): {resp.text}")

    return resp.json()


def send_kakao_text(text, access_token):
    """카카오톡 '나에게 보내기'로 텍스트 메시지를 전송한다 (안내/알림용)."""
    link_url = "https://www.elyes.co.kr/post/recruit"

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

    if resp.status_code != 200:
        raise RuntimeError(f"카카오 전송 실패 ({resp.status_code}): {resp.text}")

    return resp.json()
