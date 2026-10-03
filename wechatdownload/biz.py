from __future__ import annotations

import html
import re

from wechatdownload.urls import query_value

_BIZ_QUERY = re.compile(r"__biz=(.*?)&")
_BIZ_ATTR = re.compile(r'biz\s*=\s*"(.*?)"')
_WINDOW_BIZ = re.compile(r"window\.biz\s*=\s*(.*)?")
_QUOTED = re.compile(r"'(.*?)'")
_BIZ_CHARS = re.compile(r"^[A-Za-z0-9+/=_-]+$")


def is_valid_biz(value: str) -> bool:
    text = html.unescape((value or "").strip())
    return len(text) >= 4 and _BIZ_CHARS.fullmatch(text) is not None


def extract_biz_legacy(text: str) -> str | None:
    """4.6 的三个正则。``__biz=(.*?)&`` 要求后面还有一个 &。"""
    match = _BIZ_QUERY.search(text or "")
    if match and is_valid_biz(match.group(1)):
        return html.unescape(match.group(1))
    match = _BIZ_ATTR.search(text or "")
    if match and is_valid_biz(match.group(1)):
        return html.unescape(match.group(1))
    match = _WINDOW_BIZ.search(text or "")
    if match:
        quoted = _QUOTED.search(match.group(1) or "")
        if quoted and is_valid_biz(quoted.group(1)):
            return html.unescape(quoted.group(1))
    return None


def extract_biz(text: str) -> str | None:
    """先读查询参数，biz 在最后一个参数时也能取到。"""
    direct = query_value(text or "", "__biz")
    if is_valid_biz(direct):
        return direct
    return extract_biz_legacy(text)
