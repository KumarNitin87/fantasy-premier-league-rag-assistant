from src.rag.source_metadata import source_date, source_url


def test_source_metadata_prefers_published_timestamp_and_existing_https_link():
    item = {
        "metadata": {
            "published_at": "2026-10-20T12:00:00Z",
            "updated_at": "2026-10-21T12:00:00Z",
            "url": "https://example.test/article",
        }
    }

    assert source_date(item) == "2026-10-20T12:00:00Z"
    assert source_url(item) == "https://example.test/article"


def test_source_metadata_rejects_non_http_links():
    assert source_url({"metadata": {"url": "javascript:alert(1)"}}) == ""
