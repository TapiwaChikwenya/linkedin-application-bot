from urllib.parse import parse_qs, unquote, urlparse

from utils import (
    LinkedinUrlGenerate,
    positive_search_keyword,
    search_keyword_query,
    urlToKeywords,
)


def test_phrase_query_quotes_multi_word_keywords():
    query = search_keyword_query("Senior Data Engineer", excluded=["intern", "entry level"])
    assert query.startswith('"Senior Data Engineer"')
    assert "NOT intern" in query
    assert 'NOT "entry level"' in query


def test_exclusion_does_not_negate_the_positive_keyword():
    query = search_keyword_query("Junior Data Engineer", excluded=["junior", "intern"])
    assert "NOT junior" not in query
    assert "NOT intern" in query


def test_single_word_keyword_is_not_quoted():
    assert search_keyword_query("Databricks", excluded=[]) == "Databricks"


def test_positive_search_keyword_strips_boolean_tail():
    raw = '"Senior Data Engineer" NOT intern NOT "entry level"'
    assert positive_search_keyword(raw) == "Senior Data Engineer"


def test_generated_urls_filter_at_linkedin_not_after_open(monkeypatch):
    import config

    monkeypatch.setattr(config, "location", ["United States"])
    monkeypatch.setattr(config, "keywords", ["Senior Data Engineer"])
    monkeypatch.setattr(config, "blackListTitles", ["intern", "junior", "entry level"])
    urls = LinkedinUrlGenerate().generateUrlLinks()
    assert urls
    parsed = urlparse(urls[0])
    keywords = unquote((parse_qs(parsed.query).get("keywords") or [""])[0])
    assert keywords.startswith('"Senior Data Engineer"')
    assert "NOT intern" in keywords
    assert "NOT junior" in keywords
    assert 'NOT "entry level"' in keywords
    assert urlToKeywords(urls[0]) == ["Senior Data Engineer", "United States"]
    assert "f_AL=true" in urls[0]
