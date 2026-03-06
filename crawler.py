import html
import re
import requests
from bs4 import BeautifulSoup


API_URL = "https://www.elyes.co.kr/post/data/recruit/list"


def fetch_recruit_list(page=1, page_size=10):
    """모집공고 목록을 API에서 가져온다."""
    resp = requests.post(API_URL, data={
        "i_iNowPageNo": page,
        "i_iPageSize": page_size,
        "searchSelect": "",
        "searchKeyword": "",
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("recruitList", []), data.get("pagination", {})


def parse_html_content(raw_html):
    """HTML 인코딩된 본문을 평문 텍스트로 변환한다."""
    # 이중 HTML 엔티티 디코딩
    decoded = html.unescape(html.unescape(raw_html))
    soup = BeautifulSoup(decoded, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    # 연속 빈 줄 정리
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_title(raw_title):
    """HTML 엔티티가 포함된 제목을 디코딩한다."""
    return html.unescape(html.unescape(raw_title))


def get_latest_posts(count=5):
    """최신 글 count개를 파싱해서 반환한다."""
    items, _ = fetch_recruit_list(page=1, page_size=count)
    posts = []
    for item in items:
        post = {
            "id": item.get("nt_idx2"),
            "title": parse_title(item.get("nt_title", "")),
            "date": item.get("row_input_date", ""),
            "content_text": parse_html_content(item.get("nt_content", "")),
        }
        posts.append(post)
    return posts


if __name__ == "__main__":
    for p in get_latest_posts(3):
        print(f"[{p['date']}] {p['title']}")
        print(p["content_text"][:200])
        print("-" * 60)
