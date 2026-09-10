# -*- coding: utf-8 -*-
"""엘리스 게시글 HTML 파싱 테스트."""
import html

import crawler


def double_escape(markup):
    """엘리스 API가 내려주는 이중 이스케이프 형태로 만든다."""
    return html.escape(html.escape(markup, quote=False), quote=False)


TABLE_HTML = """<p>▣'26.04.10(금) 16시 접수 마감 집계</p>
<table>
 <tbody>
  <tr><td>타입</td><td>모집세대수</td><td>접수자수</td><td>당첨자수</td><td>예비자수</td></tr>
  <tr><td>59A</td><td>3</td><td>249</td><td>3</td><td>6</td></tr>
  <tr><td>84A</td><td>3</td><td>123</td><td>3</td><td>6</td></tr>
  <tr><td>계</td><td>6</td><td>372</td><td>6</td><td>12</td></tr>
 </tbody>
</table>
<p>※ 당첨자 및 예비당첨자는 개별통보 됩니다.</p>"""


def test_parse_html_content_renders_table_as_pipe_rows():
    text = crawler.parse_html_content(double_escape(TABLE_HTML))
    lines = text.split("\n")

    header = next(l for l in lines if "타입" in l)
    assert "모집세대수" in header and "접수자수" in header

    # 헤더 아래에 구분선이 들어간다
    sep = lines[lines.index(header) + 1]
    assert set(sep.strip()) <= {"-", "+"}

    row = next(l for l in lines if l.startswith("59A"))
    cells = [c.strip() for c in row.split("|")]
    assert cells[:5] == ["59A", "3", "249", "3", "6"]


def test_parse_html_content_strips_tags_and_collapses_blank_lines():
    text = crawler.parse_html_content(double_escape("<p>안내</p><p><br></p><p><br></p><p>끝</p>"))
    assert "<p>" not in text
    assert "\n\n\n" not in text
    assert text.startswith("안내")
    assert text.endswith("끝")


def test_parse_html_content_handles_empty_input():
    assert crawler.parse_html_content("") == ""


def test_extract_first_image_makes_absolute_url():
    raw = double_escape('<p><img src="/FileUpload/hp/a.jpg" /></p>')
    assert crawler.extract_first_image(raw) == "https://www.elyes.co.kr/FileUpload/hp/a.jpg"


def test_extract_first_image_keeps_absolute_url():
    raw = double_escape('<img src="https://cdn.example.com/a.jpg">')
    assert crawler.extract_first_image(raw) == "https://cdn.example.com/a.jpg"


def test_extract_first_image_returns_none_when_absent():
    assert crawler.extract_first_image(double_escape("<p>이미지 없음</p>")) is None


def test_parse_title_decodes_double_escaped_entities():
    assert crawler.parse_title("[사송] 재임대 모집공고 &amp;amp; 안내") == "[사송] 재임대 모집공고 & 안내"


def test_get_latest_posts_builds_post_dicts(monkeypatch):
    fake_item = {
        "nt_idx2": "12345",
        "nt_idx": "ZsrLkvMSuUrqhwwwBZ+u/w==",
        "nt_title": "[하단 롯데캐슬] 재임대 모집공고(모집기간:26.09.08~26.09.10)",
        "row_input_date": "2026.09.07",
        "nt_content": double_escape('<p>본문</p><img src="/x.jpg">'),
    }
    monkeypatch.setattr(crawler, "fetch_recruit_list", lambda page, page_size: ([fake_item], {}))

    post = crawler.get_latest_posts(count=1)[0]
    assert post["id"] == "12345"
    assert post["title"].startswith("[하단 롯데캐슬]")
    assert post["date"] == "2026.09.07"
    assert post["content_text"] == "본문"
    assert post["image_url"] == "https://www.elyes.co.kr/x.jpg"
    # base64 nt_idx의 '+', '=' 는 인코딩되어야 한다 (안 하면 서버가 공백으로 해석)
    assert "%2B" in post["detail_url"] and "%3D%3D" in post["detail_url"]
    assert "+" not in post["detail_url"]
