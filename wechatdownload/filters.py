from __future__ import annotations

from datetime import date, datetime, time

from wechatdownload.models import ArticleRef, FilterOutcome


def is_original(article: ArticleRef) -> bool:
    """常量同时出现 copyright_stat 和 copyright_type。任一为 1 视为原创。"""
    return article.copyright_stat == 1 or article.copyright_type == 1


def _published(article: ArticleRef) -> datetime | None:
    if not article.published_at:
        return None
    try:
        return datetime.fromtimestamp(article.published_at)
    except (OSError, OverflowError, ValueError):
        return None


def _format_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def decide_article(
    article: ArticleRef,
    *,
    original_only: bool = False,
    date_mode: str = "all",
    start_date: date | None = None,
    end_date: date | None = None,
) -> FilterOutcome:
    """对应 CheckBox_11、日期单选和「跳过下载 / 暂停下载」文案。

    date_mode:
    - all: 不看日期
    - skip: 窗口外的文章跳过，继续翻页
    - stop: 新于结束日的跳过；旧于开始日时暂停整个任务（历史从新到旧）
    """
    if not article.url:
        return FilterOutcome("skip", "empty_content_url", f"{article.title} 链接为空，跳过下载")
    if original_only and not is_original(article):
        return FilterOutcome("skip", "not_original", f"{article.title} 非原创文章，跳过下载")
    if date_mode == "all" or (start_date is None and end_date is None):
        return FilterOutcome("keep")
    published = _published(article)
    if published is None:
        return FilterOutcome("keep", "missing_time", f"{article.title} 发布时间为空，仍下载")
    start_at = datetime.combine(start_date, time.min) if start_date else None
    end_at = datetime.combine(end_date, time.max) if end_date else None
    formatted = _format_time(published)
    newer_than_end = end_at is not None and published > end_at
    older_than_start = start_at is not None and published < start_at
    if date_mode == "stop":
        if newer_than_end:
            return FilterOutcome("skip", "after_end_date", f"{article.title} 发布时间为{formatted}，跳过下载")
        if older_than_start:
            return FilterOutcome("stop", "before_start_date", f"{article.title} 发布时间为{formatted}，暂停下载")
        return FilterOutcome("keep")
    if newer_than_end or older_than_start:
        return FilterOutcome("skip", "outside_date", f"{article.title} 发布时间为{formatted}，跳过下载")
    return FilterOutcome("keep")


def below_min_reads(read_num: int, minimum: int) -> bool:
    return read_num < minimum
