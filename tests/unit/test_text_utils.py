import pytest

from utils.formatting.text import escape_html, safe_html, format_error, format_success


class TestEscapeHtml:
    def test_angle_brackets(self):
        assert escape_html("<script>") == "&lt;script&gt;"

    def test_ampersand(self):
        assert escape_html("a & b") == "a &amp; b"

    def test_quotes_not_escaped(self):
        assert escape_html('"hello"') == '"hello"'

    def test_plain_text_unchanged(self):
        assert escape_html("hello world") == "hello world"

    def test_non_string_cast(self):
        assert escape_html(42) == "42"

    def test_empty_string(self):
        assert escape_html("") == ""


class TestSafeHtml:
    def test_bold(self):
        result = safe_html(bold="test")
        assert "<b>test</b>" in result

    def test_italic(self):
        result = safe_html(italic="em")
        assert "<i>em</i>" in result

    def test_link_escapes_url(self):
        result = safe_html(link=("click", "https://example.com?a=1&b=2"))
        assert 'href="https://example.com?a=1&amp;b=2"' in result
        assert ">click</a>" in result

    def test_code_escapes_content(self):
        result = safe_html(code="<div>")
        assert "<code>&lt;div&gt;</code>" in result

    def test_pre_block(self):
        result = safe_html(pre="line1\nline2")
        assert "<pre>line1\nline2</pre>" in result

    def test_bullet_list(self):
        result = safe_html(bullet_list=["one", "two"])
        assert "• one" in result
        assert "• two" in result

    def test_base_text_appended_last(self):
        result = safe_html("footer", bold="header")
        lines = result.split("\n")
        assert lines[-1] == "footer"

    def test_combined(self):
        result = safe_html(
            "base", bold="B", italic="I",
            link=("link", "https://x.com"),
            code="C", pre="P", bullet_list=["item"],
        )
        assert "<b>B</b>" in result
        assert "<i>I</i>" in result
        assert "• item" in result
        assert result.endswith("base")


class TestFormatMessages:
    def test_error(self):
        assert format_error("fail") == "Ошибка! fail"

    def test_success(self):
        assert format_success("ok") == "Успешно! ok"

    def test_error_preserves_html(self):
        msg = format_error("<b>bad</b>")
        assert "<b>bad</b>" in msg
