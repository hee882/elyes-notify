import html
import re
import requests
from bs4 import BeautifulSoup


API_URL = "https://www.elyes.co.kr/post/data/recruit/list"
BASE_URL = "https://www.elyes.co.kr"


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


def extract_first_image(raw_html):
    """HTML에서 첫 번째 이미지 URL을 추출한다."""
    decoded = html.unescape(html.unescape(raw_html))
    soup = BeautifulSoup(decoded, "html.parser")
    img = soup.find("img")
    if img and img.get("src"):
        src = img["src"]
        if src.startswith("/"):
            return BASE_URL + src
        return src
    return None


def parse_html_content(raw_html):
    """HTML 인코딩된 본문을 구조화된 텍스트로 변환한다."""
    decoded = html.unescape(html.unescape(raw_html))
    soup = BeautifulSoup(decoded, "html.parser")

    # 테이블을 정렬된 텍스트로 변환
    for table in soup.find_all("table"):
        parsed_rows = []
        for tr in table.find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            parsed_rows.append(cells)

        if not parsed_rows:
            continue

        # 각 컬럼 최대 너비 계산
        col_count = max(len(r) for r in parsed_rows)
        widths = [0] * col_count
        for row in parsed_rows:
            for i, cell in enumerate(row):
                # 한글은 2칸, 영숫자는 1칸으로 계산
                w = sum(2 if ord(c) > 127 else 1 for c in cell)
                widths[i] = max(widths[i], w)

        lines = []
        for ri, row in enumerate(parsed_rows):
            parts = []
            for i in range(col_count):
                cell = row[i] if i < len(row) else ""
                cell_w = sum(2 if ord(c) > 127 else 1 for c in cell)
                pad = widths[i] - cell_w
                parts.append(cell + " " * pad)
            lines.append(" | ".join(parts))
            # 헤더 아래 구분선
            if ri == 0:
                lines.append("-+-".join("-" * w for w in widths))

        table.replace_with(BeautifulSoup("\n".join(lines) + "\n", "html.parser"))

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_title(raw_title):
    """HTML 엔티티가 포함된 제목을 디코딩한다."""
    return html.unescape(html.unescape(raw_title))


def get_latest_posts(count=10):
    """최신 글 count개를 파싱해서 반환한다."""
    items, _ = fetch_recruit_list(page=1, page_size=count)
    posts = []
    for item in items:
        raw_content = item.get("nt_content", "")
        nt_idx = item.get("nt_idx", "")
        detail_url = f"{BASE_URL}/post/recruit/detail?i_sNtCode=BHCT&nt_idx={requests.utils.quote(nt_idx)}"
        post = {
            "id": item.get("nt_idx2"),
            "title": parse_title(item.get("nt_title", "")),
            "date": item.get("row_input_date", ""),
            "content_text": parse_html_content(raw_content),
            "image_url": extract_first_image(raw_content),
            "detail_url": detail_url,
        }
        posts.append(post)
    return posts


if __name__ == "__main__":
    for p in get_latest_posts(3):
        print(f"[{p['date']}] {p['title']}")
        print(f"이미지: {p['image_url']}")
        print(p["content_text"][:300])
        print("-" * 60)
