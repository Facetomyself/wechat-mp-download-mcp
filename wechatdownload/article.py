from __future__ import annotations

import html
import re
from pathlib import Path

from wechatdownload.models import ArticleRef, ReadStats, as_int
from wechatdownload.urls import query_value

OG_TITLE = re.compile(r'<meta\s+property="og:title"\s+content="(.*?)"\s*/?>', re.I | re.S)
JS_CONTENT = re.compile(r'<div[^>]+id=["\']js_content["\']', re.I)
CDN_URL = re.compile(r"cdn_url:\s*'(.*?)'")
BODY_TAG = re.compile(r"<body\b", re.I)
NICKNAME_PATTERNS = (
    re.compile(r'nickname = htmlDecode\("([^"]+)"\)'),
    re.compile(r"nick_name:\s*JsDecode\(['\"]([^'\"]+)['\"]\)"),
    re.compile(r"nick_name:\s*'([^']+)'"),
    re.compile(r'window\.name\s*=\s*"(.*?)";'),
)
IRRELEVANT = (
    "继续滑动看下一个",
    "向上滑动看下一个",
    "预览时标签不可点",
    "微信扫一扫",
    "关注该公众号",
    "使用完整服务",
    "精选留言",
    "视频  小程序  赞",
    "使用小程序",
    "分享  留言  收藏  听过",
)


class ArticleParseError(ValueError):
    pass


def extract_title(page: str) -> str:
    match = OG_TITLE.search(page or "")
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def extract_nickname(page: str) -> str:
    for pattern in NICKNAME_PATTERNS:
        match = pattern.search(page or "")
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def remove_special_characters(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text or "", flags=re.UNICODE)
    return re.sub(r"\s+", " ", cleaned).strip()


def delete_md_irrelevant_information(text: str) -> str:
    kept = []
    for line in (text or "").splitlines():
        if any(piece in line for piece in IRRELEVANT):
            continue
        kept.append(line)
    return "\n".join(kept).strip() + ("\n" if kept else "")


def html_to_markdown(page: str) -> str:
    try:
        import html2text
    except ImportError:
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", page or "", flags=re.I | re.S)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
    else:
        converter = html2text.HTML2Text()
        converter.ignore_images = False
        converter.body_width = 0
        text = converter.handle(page or "")
    return delete_md_irrelevant_information(text)


def parse_picture_pages(page: str) -> str | None:
    urls = [html.unescape(item) for item in CDN_URL.findall(page or "")]
    if not urls:
        return None
    title = extract_title(page)
    images = "\n".join(f'<p><img src="{html.escape(item, quote=True)}" data-w="1080"></p>' for item in urls)
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1>{images}</body></html>"
    )


def select_article_html(page: str) -> tuple[str, str]:
    """图文优先。没有 js_content 时尝试图片页，再退回含 body 的原文。"""
    if JS_CONTENT.search(page or ""):
        return page, "html"
    picture = parse_picture_pages(page)
    if picture:
        return picture, "picture"
    if BODY_TAG.search(page or ""):
        return page, "text"
    raise ArticleParseError("解析纯文本失败：未找到 body 标签")


def article_filename(article: ArticleRef, suffix: str) -> str:
    from datetime import datetime

    if article.published_at:
        try:
            stamp = datetime.fromtimestamp(article.published_at).strftime("%Y-%m-%d-%H%M")
        except (OSError, OverflowError, ValueError):
            stamp = "unknown-time"
    else:
        stamp = "unknown-time"
    title = remove_special_characters(article.title) or "untitled"
    return f"{stamp}_{title}{suffix}"


def write_article_files(directory: Path, article: ArticleRef, page: str, save_markdown: bool) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    html_path = _unique(directory / article_filename(article, ".html"))
    html_path.write_text(page, encoding="utf-8")
    written = [html_path]
    if save_markdown:
        md_path = _unique(directory / article_filename(article, ".md"))
        md_path.write_text(html_to_markdown(page), encoding="utf-8")
        written.append(md_path)
    return written


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ArticleParseError(f"文件名冲突过多: {path.name}")


def build_stat_form(article_url: str) -> dict[str, str]:
    return {
        "__biz": query_value(article_url, "__biz"),
        "mid": query_value(article_url, "mid"),
        "sn": query_value(article_url, "sn"),
        "idx": query_value(article_url, "idx") or "1",
        "is_only_read": "1",
        "is_need_reward": "0",
    }


def parse_like_num(payload: dict) -> ReadStats:
    stat = payload.get("appmsgstat")
    if not isinstance(stat, dict):
        raise ArticleParseError("parse_like_num error")
    like_num = as_int(stat.get("like_num")) or 0
    old_like = as_int(stat.get("old_like_num")) or 0
    return ReadStats(
        read_num=as_int(stat.get("read_num")) or 0,
        like_num=max(like_num, old_like),
        share_num=as_int(stat.get("share_num")) or 0,
    )
