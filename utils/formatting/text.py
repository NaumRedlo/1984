import html
from typing import Optional


def escape_html(text: str) -> str:
    return html.escape(str(text), quote=False)


def safe_html(
    base_text: str = "",
    *,
    bold: Optional[str] = None,
    italic: Optional[str] = None,
    link: Optional[tuple[str, str]] = None,
    code: Optional[str] = None,
    pre: Optional[str] = None,
    bullet_list: Optional[list[str]] = None,
) -> str:
    parts = []

    if bold:
        parts.append(f"<b>{escape_html(bold)}</b>")
    if italic:
        parts.append(f"<i>{escape_html(italic)}</i>")
    if link:
        txt, url = link
        parts.append(f'<a href="{escape_html(url)}">{escape_html(txt)}</a>')
    if code:
        parts.append(f"<code>{escape_html(code)}</code>")
    if pre:
        parts.append(f"<pre>{escape_html(pre)}</pre>")
    if bullet_list:
        for item in bullet_list:
            parts.append(f"• {escape_html(item)}")

    if base_text:
        parts.append(escape_html(base_text))

    return "\n".join(parts) if parts else escape_html(base_text)


def format_length(seconds: Optional[int]) -> str:
    """Format a duration in seconds as ``m:ss`` (e.g. ``3:07``), or ``—`` if empty."""
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}" if s > 0 else "—"


def format_error(message: str, lang: str = "en") -> str:
    from utils.i18n import t
    return t("common.error_prefix", lang) + message


def format_success(message: str, lang: str = "en") -> str:
    from utils.i18n import t
    return t("common.success_prefix", lang) + message


def plural_bucket(n: int) -> str:
    """Which Slavic plural form `n` takes — `one`, `few` or `many`.

    Russian counts in threes and `1 промахов` is how a bot sounds foreign. The
    rule lives here, once, because it was written twice: the leaderboard picked
    an i18n catalog key with it and Dossier picked a word, and two copies of one
    rule are one copy and a future bug.

    English catalogues map few and many to the same string, so the buckets are
    safe for both.
    """
    if n % 10 == 1 and n % 100 != 11:
        return "one"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "few"
    return "many"


def plural(n: int, one: str, few: str, many: str) -> str:
    """The word itself, for callers that have the three forms to hand."""
    return {"one": one, "few": few, "many": many}[plural_bucket(n)]
