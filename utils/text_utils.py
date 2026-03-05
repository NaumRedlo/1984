import html
from typing import Optional

def escape_html(text: str) -> str:
    return html.escape(str(text), quote=False)

def safe_html(
    base_text: str = "",
    *,
    bold: Optional[str] = None,
    italic: Optional[str] = None,
    link: Optional[tuple[str, str]] = None,      # (текст, url)
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


def format_error(message: str) -> str:
    return f"Error! {message}"


def format_success(message: str) -> str:
    return f"Successful! {message}"
