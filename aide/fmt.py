"""Aide — Telegram message formatting.

Telegram supports a small HTML subset. Anything outside it, or a single
unclosed tag, causes the API to reject the ENTIRE message — so every send goes
through sanitise() and every send has a plain-text fallback.

Supported by Telegram: b, i, u, s, code, pre, a, tg-spoiler, blockquote.
Not supported: tables, colours, headings, lists, div/span, anything else.
"""
from __future__ import annotations

import html
import re

# Tags Telegram actually understands. Everything else gets stripped.
ALLOWED = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
           "code", "pre", "a", "tg-spoiler", "blockquote"}

_TAG_RE = re.compile(r"</?([a-zA-Z-]+)(\s[^>]*)?>")


def esc(text: str) -> str:
    """Escape user/tool content so it can't break the markup."""
    return html.escape(str(text), quote=False)


_DANGEROUS = re.compile(r"(?is)<(script|style)\b.*?</\1\s*>")


def sanitise(text: str) -> str:
    """Strip disallowed tags and balance the allowed ones.

    An unclosed <b> makes Telegram reject the whole message with a 400, so
    this closes anything left open and drops stray closing tags.
    """
    # Drop script/style including their contents — keeping the inner text of
    # these would leak code into the message.
    text = _DANGEROUS.sub("", text)

    # Remove other unsupported tags, keeping their inner text.
    def _strip(m: re.Match) -> str:
        tag = m.group(1).lower()
        return m.group(0) if tag in ALLOWED else ""

    text = _TAG_RE.sub(_strip, text)

    # Balance.
    stack: list[str] = []
    out: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
        tag = m.group(1).lower()
        closing = m.group(0).startswith("</")
        if closing:
            if tag in stack:
                # close everything opened after it, then it
                while stack and stack[-1] != tag:
                    out.append(f"</{stack.pop()}>")
                stack.pop()
                out.append(f"</{tag}>")
            # stray closing tag: drop it
        else:
            stack.append(tag)
            out.append(m.group(0))
    out.append(text[pos:])
    while stack:
        out.append(f"</{stack.pop()}>")
    return "".join(out)


def strip_all(text: str) -> str:
    """Plain-text fallback when Telegram rejects the formatted version."""
    text = _TAG_RE.sub("", text)
    return html.unescape(text)


# ---------------------------------------------------------------------------
# Brief rendering
# ---------------------------------------------------------------------------

def task_line(t: dict) -> str:
    """One task, formatted. IDs in <code> so they're tappable-to-copy."""
    bits = [f"<code>#{t['id']}</code> {esc(t['title'])}"]
    if t.get("due"):
        bits.append(f"<i>due {esc(t['due'])}</i>")
    if t.get("defer_count", 0) >= 3:
        bits.append(f"<b>×{t['defer_count']} deferred</b>")
    return " · ".join(bits)


def header(when, open_count: int) -> str:
    return f"<b>{when.strftime('%a %d %b')}</b> · {open_count} open"


SECTION = {
    "calendar": "📅",
    "carry": "⏳",
    "top3": "🎯",
    "inbox": "📬",
    "health": "📊",
}
