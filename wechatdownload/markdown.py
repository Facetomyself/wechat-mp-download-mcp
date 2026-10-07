"""把公众号正文 HTML 转成 Markdown。只使用标准库。

转换范围是 ``#js_content``。没有正文节点时用 ``body``（图片页）。
页面壳、脚本和代码块窗口按钮不进入结果。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

HEADINGS = {f"h{level}" for level in range(1, 7)}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
SKIP_TAGS = {
    "script",
    "style",
    "svg",
    "iframe",
    "noscript",
    "canvas",
    "video",
    "audio",
    "button",
    "form",
    "textarea",
    "select",
    "mpvoice",
    "mpvideosnap",
    "qqmusic",
}
INLINE_TAGS = {
    "span",
    "strong",
    "b",
    "em",
    "i",
    "code",
    "a",
    "sub",
    "sup",
    "label",
    "font",
    "small",
    "u",
    "s",
    "del",
    "mark",
    "br",
    "img",
    "wbr",
    "mp-style-type",
    "acronym",
}
LANG_ALIAS = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "rs": "rust",
    "sh": "bash",
    "shell": "bash",
    "kt": "kotlin",
    "yml": "yaml",
    "md": "markdown",
}


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]
        self.skip_tag = ""
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=tag.lower() in VOID_TAGS)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, void=True)

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], void: bool) -> None:
        tag = tag.lower()
        if self.skip_tag:
            if not void and tag == self.skip_tag:
                self.skip_depth += 1
            return
        if tag in SKIP_TAGS:
            if not void:
                self.skip_tag = tag
                self.skip_depth = 1
            return
        node = _Node(tag, {key.lower(): value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if not void:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth <= 0:
                    self.skip_tag = ""
                    self.skip_depth = 0
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self.skip_tag or not data:
            return
        self.stack[-1].children.append(data)


def render_article_markdown(page: str, title: str = "") -> str:
    page = page or ""
    fragment = _slice_js_content(page)
    if fragment is not None:
        root = _parse(f'<div id="js_content">{fragment}</div>')
        content = _find_id(root, "js_content") or root
        heading = title.strip()
    else:
        root = _parse(page)
        content = _find_tag(root, "body") or root
        heading = (title or _title_from_tree(root)).strip()
    body = _join(_blocks(content), trailing=False)
    if heading:
        first = body.lstrip().splitlines()[0] if body else ""
        if first != f"# {heading}":
            body = f"# {heading}\n\n{body}" if body else f"# {heading}"
    return body.strip() + ("\n" if body.strip() or heading else "")


def _slice_js_content(page: str) -> str | None:
    """Cut out #js_content without parsing scripts. None means the node is absent."""
    index = 0
    while index < len(page):
        start = _next_markup(page, index)
        if start < 0:
            return None
        if not page[start : start + 4].lower() == "<div":
            index = start + 1
            continue
        end = _tag_end(page, start)
        if end < 0:
            return None
        open_tag = page[start:end]
        if re.search(r"\bid=(['\"])js_content\1", open_tag, re.I):
            return _div_inner(page, end + 1)
        index = end + 1
    return None


def _div_inner(page: str, inner_start: int) -> str:
    depth = 1
    index = inner_start
    while index < len(page):
        start = _next_markup(page, index)
        if start < 0:
            break
        lowered = page[start : start + 5].lower()
        if lowered.startswith("<div"):
            end = _tag_end(page, start)
            if end < 0:
                break
            if not page[start : end + 1].rstrip().endswith("/>"):
                depth += 1
            index = end + 1
            continue
        if lowered.startswith("</div"):
            end = _tag_end(page, start)
            if end < 0:
                break
            depth -= 1
            if depth == 0:
                return page[inner_start:start]
            index = end + 1
            continue
        index = start + 1
    return page[inner_start:]


def _next_markup(page: str, index: int) -> int:
    while index < len(page):
        start = page.find("<", index)
        if start < 0:
            return -1
        name = page[start : start + 8].lower()
        if name.startswith("<script") or name.startswith("<style"):
            kind = "script" if name.startswith("<script") else "style"
            close = re.search(rf"</{kind}\s*>", page[start:], re.I)
            if close is None:
                return -1
            index = start + close.end()
            continue
        return start
    return -1


def _tag_end(page: str, start: int) -> int:
    quote = ""
    for index in range(start, len(page)):
        char = page[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
        elif char == ">":
            return index
    return -1


def _parse(page: str) -> _Node:
    builder = _Builder()
    builder.feed(page)
    builder.close()
    return builder.root


def _find_id(node: _Node, node_id: str) -> _Node | None:
    if node.attrs.get("id") == node_id:
        return node
    for child in node.children:
        if isinstance(child, _Node):
            found = _find_id(child, node_id)
            if found is not None:
                return found
    return None


def _find_tag(node: _Node, tag: str) -> _Node | None:
    if node.tag == tag:
        return node
    for child in node.children:
        if isinstance(child, _Node):
            found = _find_tag(child, tag)
            if found is not None:
                return found
    return None


def _title_from_tree(root: _Node) -> str:
    node = _find_id(root, "activity-name")
    if node is None:
        return ""
    return _clean_paragraph(_inline(node))


def _dropped(node: _Node) -> bool:
    classes = node.attrs.get("class", "")
    if "code-snippet__line-index" in classes or "line-numbers" in classes.split():
        return True
    style = (node.attrs.get("style") or "").replace(" ", "")
    return "display:none" in style


def _blocks(node: _Node) -> list[str]:
    if _dropped(node) or node.tag in SKIP_TAGS:
        return []
    tag = node.tag
    if tag in HEADINGS:
        text = _clean_paragraph(_inline(node))
        if not text:
            return []
        level = int(tag[1])
        return [f"{'#' * level} {text}"]
    if tag == "pre":
        code = _pre_text(node)
        if not code:
            return []
        return [_fence(code, _code_lang(node))]
    if tag == "img":
        image = _image(node)
        return [image] if image else []
    if tag == "hr":
        return ["---"]
    if tag in ("ul", "ol"):
        listing = _list_block(node, ordered=tag == "ol")
        return [listing] if listing else []
    if tag == "blockquote":
        return _quote(node)
    if tag == "table":
        table = _table(node)
        return [table] if table else []
    if tag == "figure":
        return _figure(node)
    return _flow(node.children)


def _flow(children: list[_Node | str]) -> list[str]:
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        text = _clean_paragraph("".join(buf))
        buf.clear()
        if text:
            out.append(text)

    for child in children:
        if isinstance(child, str) or _is_inline(child):
            _append_inline(buf, _inline(child))
            continue
        flush()
        out.extend(_blocks(child))
    flush()
    return out


def _is_inline(node: _Node) -> bool:
    return node.tag in INLINE_TAGS or node.tag in SKIP_TAGS or _dropped(node)


def _inline(child: _Node | str) -> str:
    if isinstance(child, str):
        text = child.replace("\xa0", " ").replace("\u200b", "")
        text = text.replace("\r", "").replace("\n", " ")
        return re.sub(r"[ \t]+", " ", text)
    if _dropped(child) or child.tag in SKIP_TAGS:
        return ""
    if child.tag == "br":
        return "\n"
    if child.tag == "img":
        return _image(child)
    if child.tag == "code":
        return _ticks(_code_text(child))
    if child.tag in ("strong", "b"):
        return _wrap_emphasis(_join_inline(child.children), "**")
    if child.tag in ("em", "i"):
        return _wrap_emphasis(_join_inline(child.children), "*")
    if child.tag == "a":
        return _link(child)
    return _join_inline(child.children)


def _join_inline(children: list[_Node | str]) -> str:
    parts: list[str] = []
    for child in children:
        _append_inline(parts, _inline(child))
    return "".join(parts)


def _append_inline(parts: list[str], piece: str) -> None:
    if not piece:
        return
    if parts and _needs_gap(parts[-1], piece):
        parts.append(" ")
    parts.append(piece)


def _needs_gap(previous: str, nxt: str) -> bool:
    """相邻的行内代码、加粗会粘成非法 Markdown。中间补一个空格。"""
    prev_code = previous.endswith("`")
    next_code = nxt.startswith("`")
    prev_mark = previous.endswith("*")
    next_mark = nxt.startswith("*")
    return (prev_code and next_code) or (prev_code and next_mark) or (prev_mark and next_code)


def _wrap_emphasis(text: str, marker: str) -> str:
    if not text.strip() or "`" in text or text.strip().startswith(marker):
        return text
    core = text.strip()
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{marker}{core}{marker}{trail}"


def _link(node: _Node) -> str:
    href = (node.attrs.get("href") or "").strip()
    label = _clean_paragraph(_join_inline(node.children))
    if not href or href.lower().startswith("javascript:"):
        return label
    return f"[{label or href}]({href})"


def _image(node: _Node) -> str:
    src = (node.attrs.get("src") or node.attrs.get("data-src") or "").strip()
    if not src:
        return ""
    alt = (node.attrs.get("alt") or "").replace("\n", " ").strip()
    alt = alt.replace("[", "\\[").replace("]", "\\]")
    return f"![{alt}]({src})"


def _code_text(node: _Node) -> str:
    parts: list[str] = []

    def walk(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                parts.append(child.replace("\xa0", " ").replace("\u200b", ""))
            elif child.tag == "br":
                parts.append(" ")
            elif child.tag not in SKIP_TAGS and not _dropped(child):
                walk(child)

    walk(node)
    return "".join(parts).strip()


def _ticks(text: str) -> str:
    if not text:
        return ""
    width = 1
    while "`" * width in text:
        width += 1
    marker = "`" * width
    if width > 1 or text[:1] == "`" or text[-1:] == "`":
        return f"{marker} {text} {marker}"
    return f"{marker}{text}{marker}"


def _pre_text(node: _Node) -> str:
    parts: list[str] = []

    def walk(current: _Node) -> None:
        for child in current.children:
            if isinstance(child, str):
                parts.append(child.replace("\xa0", " ").replace("\u200b", ""))
            elif child.tag == "br":
                parts.append("\n")
            elif child.tag not in SKIP_TAGS and not _dropped(child):
                walk(child)

    walk(node)
    text = "".join(parts).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _code_lang(node: _Node) -> str:
    classes: list[str] = []

    def walk(current: _Node) -> None:
        if current.attrs.get("class"):
            classes.append(current.attrs["class"])
        declared = current.attrs.get("data-lang") or ""
        if declared and current.tag in ("pre", "code"):
            classes.append("language-" + declared)
        for child in current.children:
            if isinstance(child, _Node) and child.tag in ("pre", "code", "span"):
                walk(child)

    walk(node)
    for class_name in classes:
        for token in class_name.split():
            key = ""
            if token.startswith("language-") or token.startswith("lang-"):
                key = token.split("-", 1)[1].lower()
            elif token.startswith("code-snippet__"):
                key = token.split("__", 1)[1].lower()
                if key in ("fix", "line-index"):
                    key = ""
            if not key:
                continue
            return LANG_ALIAS.get(key, key)
    return ""


def _fence(code: str, lang: str) -> str:
    marker = "```"
    while marker in code:
        marker += "`"
    return f"{marker}{lang}\n{code}\n{marker}"


def _list_block(node: _Node, ordered: bool) -> str:
    items: list[str] = []
    index = 1
    for child in node.children:
        if not isinstance(child, _Node) or child.tag != "li" or _dropped(child):
            continue
        blocks = _flow(child.children)
        if not blocks:
            continue
        first = blocks[0]
        if ordered:
            matched = re.match(r"^(\d{1,4})\s*[.、．)]\s+", first)
            number = matched.group(1) if matched else str(index)
            if matched:
                blocks[0] = first[matched.end() :]
            prefix = f"{number}. "
        else:
            blocks[0] = re.sub(r"^(?:[•·●▪◦]\s*|[-*]\s+)", "", first, count=1)
            prefix = "- "
        items.append(_indent_item(prefix, blocks))
        index += 1
    return "\n".join(items)


def _indent_item(prefix: str, blocks: list[str]) -> str:
    text = _join(blocks, trailing=False)
    lines = text.split("\n")
    pad = " " * len(prefix)
    out = [prefix + lines[0]]
    out.extend((pad + line) if line else "" for line in lines[1:])
    return "\n".join(out)


def _quote(node: _Node) -> list[str]:
    inner = _join(_flow(node.children), trailing=False)
    if not inner:
        return []
    lines = [("> " + line) if line else ">" for line in inner.split("\n")]
    return ["\n".join(lines)]


def _table(node: _Node) -> str:
    rows: list[list[str]] = []

    def walk(current: _Node) -> None:
        for child in current.children:
            if not isinstance(child, _Node):
                continue
            if child.tag != "tr":
                walk(child)
                continue
            cells: list[str] = []
            for cell in child.children:
                if not isinstance(cell, _Node) or cell.tag not in ("td", "th"):
                    continue
                text = _clean_paragraph(_inline(cell)).replace("\n", " ").replace("|", "\\|").strip()
                cells.append(text)
            if cells:
                rows.append(cells)

    walk(node)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    for row in rows:
        row.extend("" for _ in range(width - len(row)))

    def format_row(row: list[str]) -> str:
        return "| " + " | ".join(row) + " |"

    lines = [format_row(rows[0]), "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend(format_row(row) for row in rows[1:])
    return "\n".join(lines)


def _figure(node: _Node) -> list[str]:
    out: list[str] = []

    def walk(current: _Node) -> None:
        for child in current.children:
            if not isinstance(child, _Node) or child.tag in SKIP_TAGS or _dropped(child):
                continue
            if child.tag == "figcaption":
                caption = _clean_paragraph(_inline(child))
                if caption:
                    out.append(caption)
            elif child.tag == "img":
                image = _image(child)
                if image:
                    out.append(image)
            elif child.tag in ("pre", "table", "ul", "ol", "blockquote") or child.tag in HEADINGS:
                out.extend(_blocks(child))
            else:
                walk(child)

    walk(node)
    return out


def _clean_paragraph(text: str) -> str:
    text = text.replace("\xa0", " ")
    parts = re.split(r"(`[^`]*`)", text)
    merged: list[str] = []
    for index, part in enumerate(parts):
        if index % 2 == 1:
            merged.append(part)
        else:
            merged.append(re.sub(r"[ \t]{2,}", " ", part))
    lines: list[str] = []
    blank = False
    for raw in "".join(merged).split("\n"):
        line = raw.strip()
        if not line:
            if lines and not blank:
                lines.append("")
            blank = True
            continue
        lines.append(line)
        blank = False
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def _join(blocks: list[str], trailing: bool) -> str:
    cleaned = [block.strip("\n") for block in blocks if block and block.strip()]
    text = "\n\n".join(cleaned)
    if trailing and text:
        text += "\n"
    return text
