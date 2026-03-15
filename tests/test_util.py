from noteropdf.util import parse_notion_page_id_from_url


def test_parse_canonical_uuid_from_url():
    url = "https://www.notion.so/workspace/Page-12345678-1234-1234-1234-1234567890ab"
    assert parse_notion_page_id_from_url(url) == "12345678-1234-1234-1234-1234567890ab"


def test_parse_compact_notion_id_from_url():
    url = "https://www.notion.so/workspace/Page-123456781234123412341234567890ab"
    assert parse_notion_page_id_from_url(url) == "12345678-1234-1234-1234-1234567890ab"


def test_parse_invalid_url_returns_none():
    assert parse_notion_page_id_from_url("https://example.com/no-id-here") is None
