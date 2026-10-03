from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import urlparse

from wechatdownload.models import ArticleRef, as_int
from wechatdownload.urls import build_album_url, is_collection_url, normalize_content_url, query_value

ALBUM_LINK = re.compile(r"https://mp\.weixin\.qq\.com/mp/appmsgalbum\?[^\"'\s<]+")
MSGID_RE = re.compile(r"msgid:\s*'(.*?)'")
ITEMIDX_RE = re.compile(r"itemidx:\s*'(.*?)'")


class AlbumError(ValueError):
    pass


def find_valid_params(text: str) -> dict[str, str]:
    raw = text or ""
    album_id = query_value(raw, "album_id")
    msgid = query_value(raw, "msgid") or query_value(raw, "from_msgid")
    itemidx = query_value(raw, "itemidx") or query_value(raw, "item_idx")
    biz = query_value(raw, "__biz")
    if not album_id:
        match = re.search(r"album_id=([^&\"'\s]+)", raw)
        album_id = match.group(1) if match else ""
    if not msgid:
        match = MSGID_RE.search(raw)
        msgid = match.group(1) if match else ""
    if not itemidx:
        match = ITEMIDX_RE.search(raw)
        itemidx = match.group(1) if match else ""
    if not biz:
        match = re.search(r"__biz=([^&\"'\s]+)", raw)
        biz = match.group(1) if match else ""
    return {"album_id": album_id, "msgid": msgid, "itemidx": itemidx, "__biz": biz}


def parse_album_response(body: str) -> tuple[list[ArticleRef], bool | None]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise AlbumError(f"合集下载失败: {exc}") from exc
    if not isinstance(data, dict):
        raise AlbumError("合集下载失败")
    resp = data.get("getalbum_resp")
    if not isinstance(resp, dict):
        raise AlbumError("合集下载失败")
    raw_list = resp.get("article_list") or []
    if not isinstance(raw_list, list):
        raise AlbumError("合集下载失败")
    articles: list[ArticleRef] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        published = as_int(item.get("create_time") or item.get("datetime"))
        articles.append(
            ArticleRef(
                title=str(item.get("title") or ""),
                url=normalize_content_url(str(item.get("url") or item.get("content_url") or "")),
                published_at=published,
                copyright_stat=as_int(item.get("copyright_stat")),
                copyright_type=as_int(item.get("copyright_type")),
                source="album",
                msg_id=str(item.get("msgid") or ""),
                item_idx=str(item.get("itemidx") or ""),
            )
        )
    flag = resp.get("continue_flag")
    if flag is None:
        can_continue = None
    else:
        can_continue = str(flag) not in {"0", "false", "False"}
    return articles, can_continue


def album_links_in_html(page: str) -> list[str]:
    return list(dict.fromkeys(ALBUM_LINK.findall(page or "")))


def crawl_album(
    page_or_url: str,
    *,
    biz: str,
    uin: str,
    key: str,
    pass_ticket: str,
    get_text: Callable[[str], str],
    count: int = 10,
    reverse: bool = False,
    max_pages: int | None = None,
) -> list[ArticleRef]:
    """历史 getmsg 不包含合集。合集和主页走 action=getalbum。"""
    text = page_or_url
    if text.startswith("http") and "mp/homepage" in text:
        text = get_text(text)
    if "<" in text:
        links = album_links_in_html(text)
        if not links:
            raise AlbumError("无效的链接")
        articles: list[ArticleRef] = []
        for link in links:
            articles.extend(
                crawl_album(
                    link,
                    biz=biz or find_valid_params(link)["__biz"],
                    uin=uin,
                    key=key,
                    pass_ticket=pass_ticket,
                    get_text=get_text,
                    count=count,
                    reverse=reverse,
                    max_pages=max_pages,
                )
            )
        return articles
    params = find_valid_params(text)
    album_id = params["album_id"]
    use_biz = biz or params["__biz"]
    if not album_id or not use_biz:
        raise AlbumError("参数检查失败: 无效的链接")
    begin_msgid = params["msgid"]
    begin_itemidx = params["itemidx"] or "0"
    seen: set[str] = set()
    collected: list[ArticleRef] = []
    pages = 0
    while True:
        if max_pages is not None and pages >= max_pages:
            break
        url = build_album_url(
            use_biz,
            album_id,
            begin_msgid,
            begin_itemidx,
            uin,
            key,
            pass_ticket,
            count=count,
            reverse=reverse,
        )
        articles, can_continue = parse_album_response(get_text(url))
        pages += 1
        fresh = 0
        for article in articles:
            if article.identity in seen:
                continue
            seen.add(article.identity)
            collected.append(article)
            fresh += 1
        if can_continue is False or fresh == 0 or not articles:
            break
        last = articles[-1]
        if last.msg_id == begin_msgid and last.item_idx == begin_itemidx:
            break
        begin_msgid = last.msg_id
        begin_itemidx = last.item_idx or begin_itemidx
        if can_continue is None and len(articles) < count:
            break
    return collected


def looks_like_collection(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    return is_collection_url(url)
