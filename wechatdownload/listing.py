from __future__ import annotations

import json

from wechatdownload.models import ArticleRef, HistoryPage, ParserDiff, as_int
from wechatdownload.urls import normalize_content_url

# 4.6 常量里的原样切片标记。next_offset 只出现在结束标记中，没有同名变量。
LEGACY_START = 'general_msg_list":"'
LEGACY_END = '","next_offset'


class ParseError(ValueError):
    pass


def parse_getmsg_legacy(body: str) -> list[dict]:
    """按 4.6 的字符串切片取出 general_msg_list，再 json.loads。

    标记必须紧挨着：开始是 ``general_msg_list":"``，结束是 ``","next_offset``。
    中间多了别的字段、next_offset 写在前面、或者 general_msg_list 不是字符串时，整页失败。
    """
    start = body.find(LEGACY_START)
    if start < 0:
        raise ParseError("切片没有找到 general_msg_list 字符串标记")
    start += len(LEGACY_START)
    end = body.find(LEGACY_END, start)
    if end < 0:
        raise ParseError("切片没有找到 next_offset 结束标记")
    raw = body[start:end]
    try:
        decoded = json.loads('"' + raw + '"')
    except json.JSONDecodeError as exc:
        raise ParseError(f"切片内容不是 JSON 字符串: {exc}") from exc
    if isinstance(decoded, str):
        try:
            payload = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ParseError(f"general_msg_list 内层 JSON 失败: {exc}") from exc
    elif isinstance(decoded, dict):
        payload = decoded
    else:
        raise ParseError("general_msg_list 类型无法识别")
    items = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ParseError("general_msg_list 里没有 list")
    return [item for item in items if isinstance(item, dict)]


def _flag(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value not in {"0", "false", "False"}
    return value != 0 and bool(value)


def _ret_of(data: dict) -> object:
    if "ret" in data:
        return data.get("ret")
    base = data.get("base_resp")
    if isinstance(base, dict) and "ret" in base:
        return base.get("ret")
    return 0


def parse_getmsg(body: str) -> HistoryPage:
    """解析完整 getmsg JSON。general_msg_list 可以是字符串或对象。"""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ParseError(f"getmsg 不是 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ParseError("getmsg 顶层不是对象")
    if "general_msg_list" not in data and "list" not in data:
        raise ParseError("响应里没有 general_msg_list")
    raw_list = data.get("general_msg_list")
    if isinstance(raw_list, str):
        if raw_list == "":
            items: list[dict] = []
        else:
            try:
                payload = json.loads(raw_list)
            except json.JSONDecodeError as exc:
                raise ParseError(f"general_msg_list 字符串无法解析: {exc}") from exc
            raw_items = payload.get("list") if isinstance(payload, dict) else None
            if not isinstance(raw_items, list):
                raise ParseError("general_msg_list 里没有 list")
            items = [item for item in raw_items if isinstance(item, dict)]
    elif isinstance(raw_list, dict):
        raw_items = raw_list.get("list") or []
        if not isinstance(raw_items, list):
            raise ParseError("general_msg_list.list 不是数组")
        items = [item for item in raw_items if isinstance(item, dict)]
    elif raw_list is None and isinstance(data.get("list"), list):
        items = [item for item in data["list"] if isinstance(item, dict)]
    else:
        raise ParseError("general_msg_list 类型无法识别")
    next_offset = as_int(data.get("next_offset"))
    return HistoryPage(
        items=items,
        next_offset=next_offset,
        can_continue=_flag(data.get("can_msg_continue")),
        ret=_ret_of(data),
        errmsg=str(data.get("errmsg") or ""),
    )


def _article_from_node(node: dict, source: str, published_at: int | None, msg_id: str) -> ArticleRef:
    return ArticleRef(
        title=str(node.get("title") or ""),
        url=normalize_content_url(str(node.get("content_url") or "")),
        published_at=published_at,
        copyright_stat=as_int(node.get("copyright_stat")),
        copyright_type=as_int(node.get("copyright_type")),
        source=source,
        item_show_type=as_int(node.get("item_show_type")),
        msg_id=str(node.get("msgid") or node.get("msg_id") or msg_id or ""),
        item_idx=str(node.get("itemidx") or node.get("item_idx") or node.get("idx") or ""),
    )


def flatten_message(message: dict) -> list[ArticleRef]:
    """展开主条和 multi_app_msg_item_list / app_msg_ext_info_list。

    空 content_url 也保留，调用方用 reason 记下来，避免副条悄悄消失。
    """
    ext = message.get("app_msg_ext_info")
    if not isinstance(ext, dict):
        return []
    comm = message.get("comm_msg_info") if isinstance(message.get("comm_msg_info"), dict) else {}
    published_at = as_int(comm.get("datetime"))
    msg_id = str(comm.get("id") or "")
    nodes: list[tuple[str, dict]] = [("main", ext)]
    for key, source in (("multi_app_msg_item_list", "multi"), ("app_msg_ext_info_list", "ext_list")):
        children = ext.get(key) or []
        if isinstance(children, list):
            nodes.extend((source, child) for child in children if isinstance(child, dict))
    articles: list[ArticleRef] = []
    seen: set[str] = set()
    for source, node in nodes:
        article = _article_from_node(node, source, published_at, msg_id)
        if article.identity in seen:
            continue
        seen.add(article.identity)
        articles.append(article)
    return articles


def flatten_messages(messages: list[dict]) -> list[ArticleRef]:
    articles: list[ArticleRef] = []
    for message in messages:
        articles.extend(flatten_message(message))
    return articles


def diff_parsers(body: str) -> ParserDiff:
    """同一份 getmsg 响应用两种解析器展开，列出只有修正解析能看到的文章。"""
    legacy_error: str | None = None
    legacy_ids: list[str] = []
    try:
        legacy_ids = [item.identity for item in flatten_messages(parse_getmsg_legacy(body))]
    except ParseError as exc:
        legacy_error = str(exc)
    fixed_error: str | None = None
    fixed_ids: list[str] = []
    page: HistoryPage | None = None
    try:
        page = parse_getmsg(body)
        fixed_ids = [item.identity for item in flatten_messages(page.items)]
    except ParseError as exc:
        fixed_error = str(exc)
    legacy_set = set(legacy_ids)
    fixed_set = set(fixed_ids)
    return ParserDiff(
        legacy_error=legacy_error,
        legacy_ids=legacy_ids,
        fixed_ids=fixed_ids,
        only_fixed=[item for item in fixed_ids if item not in legacy_set],
        only_legacy=[item for item in legacy_ids if item not in fixed_set],
        next_offset=None if page is None else page.next_offset,
        can_continue=None if page is None else page.can_continue,
        fixed_error=fixed_error,
    )


def ret_ok(ret: object) -> bool:
    return ret in (None, 0, "0")
