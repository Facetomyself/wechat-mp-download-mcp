from __future__ import annotations

import html
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

KEEP_PARAMS = ("__biz", "mid", "idx", "sn", "chksm")

GETMSG_PREFIX = "https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz="
GETMSG_OFFSET = "&f=json&offset="
GETMSG_TAIL = "&count={count}&is_ok=1&scene=124&uin="
HOME_PREFIX = "https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz="
ALBUM_API = "https://mp.weixin.qq.com/mp/appmsgalbum"
STAT_URL = "https://mp.weixin.qq.com/mp/getappmsgext"
KEY_EXPIRED_TEXT = "请在微信客户端打开链接"


def normalize_content_url(url: str) -> str:
    """还原 content_url 里的转义，并只保留 4.6 常量里出现的参数名。"""
    text = html.unescape(url or "")
    text = text.replace("\\/", "/").replace("&amp;", "&").strip()
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith("http://mp.weixin.qq.com"):
        text = "https://" + text[len("http://") :]
    if not text:
        return ""
    return keep_only_params(text)


def keep_only_params(url: str, names: tuple[str, ...] = KEEP_PARAMS) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key in names]
    if not kept:
        return url
    return urlunparse(parsed._replace(query=urlencode(kept)))


def build_getmsg_url(
    biz: str,
    uin: str,
    key: str,
    pass_ticket: str,
    offset: int,
    count: int = 10,
    poc_token: str = "",
) -> str:
    url = (
        f"{GETMSG_PREFIX}{biz}"
        f"{GETMSG_OFFSET}{offset}"
        f"{GETMSG_TAIL.format(count=count)}{uin}"
        f"&key={key}&pass_ticket={pass_ticket}"
    )
    if poc_token:
        url += f"&poc_token={poc_token}"
    return url


def build_home_url(biz: str, uin: str = "", key: str = "", pass_ticket: str = "", poc_token: str = "") -> str:
    url = f"{HOME_PREFIX}{biz}&scene=124"
    if uin:
        url += f"&uin={uin}&key={key}&pass_ticket={pass_ticket}"
    if poc_token:
        url += f"&poc_token={poc_token}"
    if "#wechat_redirect" not in url:
        url += "#wechat_redirect"
    return url


def build_album_url(
    biz: str,
    album_id: str,
    begin_msgid: str,
    begin_itemidx: str,
    uin: str,
    key: str,
    pass_ticket: str,
    count: int = 10,
    reverse: bool = False,
) -> str:
    query = urlencode(
        {
            "action": "getalbum",
            "__biz": biz,
            "album_id": album_id,
            "count": str(count),
            "begin_msgid": begin_msgid,
            "begin_itemidx": begin_itemidx,
            "is_reverse": "1" if reverse else "0",
            "uin": uin,
            "key": key,
            "pass_ticket": pass_ticket,
            "f": "json",
        }
    )
    return f"{ALBUM_API}?{query}"


def is_collection_url(url: str) -> bool:
    return "mp.weixin.qq.com/mp/appmsgalbum" in url or "mp.weixin.qq.com/mp/homepage" in url


def query_value(url: str, name: str) -> str:
    values = [value for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True) if key == name]
    return html.unescape(values[-1]) if values else ""
