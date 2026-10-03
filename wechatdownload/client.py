from __future__ import annotations

import csv
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wechatdownload.article import (
    ArticleParseError,
    build_stat_form,
    parse_like_num,
    select_article_html,
    write_article_files,
)
from wechatdownload.filters import below_min_reads, decide_article
from wechatdownload.http import ARTICLE_UA, LIST_UA
from wechatdownload.listing import flatten_messages, parse_getmsg, parse_getmsg_legacy, ret_ok
from wechatdownload.listing import ParseError
from wechatdownload.models import CrawlRow, HistoryPage
from wechatdownload.urls import KEY_EXPIRED_TEXT, STAT_URL, build_getmsg_url


@dataclass
class CrawlOptions:
    parser: str = "fixed"
    offset_mode: str = "server"
    count: int = 10
    start_page: int = 1
    max_pages: int | None = None
    delay_seconds: float = 0
    original_only: bool = False
    date_mode: str = "all"
    start_date: date | None = None
    end_date: date | None = None
    min_reads: int | None = None
    strict_reads: bool = False
    fetch_stats: bool = False
    download: bool = False
    save_markdown: bool = False
    save_pages: bool = False
    out_dir: Path | None = None
    start_offset: int | None = None
    cancel_event: threading.Event | None = field(default=None, compare=False)


class HistoryCrawler:
    def __init__(self, biz: str, uin: str, key: str, pass_ticket: str, transport, options: CrawlOptions, poc_token: str = ""):
        self.biz = biz
        self.uin = uin
        self.key = key
        self.pass_ticket = pass_ticket
        self.poc_token = poc_token
        self.transport = transport
        self.options = options

    def run(self) -> list[CrawlRow]:
        rows: list[CrawlRow] = []
        if self.options.start_offset is not None:
            offset = self.options.start_offset
        else:
            offset = (max(self.options.start_page, 1) - 1) * self.options.count
        seen_offsets: set[int] = set()
        pages = 0
        while True:
            if self.options.cancel_event is not None and self.options.cancel_event.is_set():
                rows.append(CrawlRow("page", "stop", "cancelled", offset=offset, message="任务已取消"))
                break
            if self.options.max_pages is not None and pages >= self.options.max_pages:
                break
            if offset in seen_offsets:
                rows.append(CrawlRow("page", "stop", "offset_loop", offset=offset, message="分页偏移重复"))
                break
            seen_offsets.add(offset)
            url = build_getmsg_url(
                self.biz,
                self.uin,
                self.key,
                self.pass_ticket,
                offset,
                count=self.options.count,
                poc_token=self.poc_token,
            )
            try:
                body = self.transport.get_text(url, LIST_UA)
            except TypeError:
                body = self.transport.get_text(url)
            except Exception as exc:
                rows.append(CrawlRow("page", "stop", "request_failed", offset=offset, message=str(exc)))
                break
            pages += 1
            self._save_page(offset, body)
            if KEY_EXPIRED_TEXT in body and "general_msg_list" not in body:
                rows.append(CrawlRow("page", "stop", "key_expired", offset=offset, message=KEY_EXPIRED_TEXT))
                break
            try:
                page = self._parse_page(body, offset)
            except ParseError as exc:
                rows.append(CrawlRow("page", "stop", "legacy_parse" if self.options.parser == "legacy" else "parse", offset=offset, message=str(exc)))
                break
            if not ret_ok(page.ret):
                rows.append(CrawlRow("page", "stop", "api_ret", offset=offset, message=f"ret={page.ret} {page.errmsg}".strip()))
                break
            if not page.items:
                rows.append(CrawlRow("page", "stop", "empty_page", offset=offset, message="本页没有消息"))
                break
            if self._consume_page(page, rows):
                break
            nxt = self._next_offset(offset, page)
            if nxt is None:
                break
            offset = nxt
            if self.options.delay_seconds > 0:
                time.sleep(self.options.delay_seconds)
        if self.options.out_dir is not None:
            write_manifest(self.options.out_dir / "download_manifest.csv", rows)
        return rows

    def _parse_page(self, body: str, offset: int) -> HistoryPage:
        if self.options.parser == "legacy":
            items = parse_getmsg_legacy(body)
            return HistoryPage(items=items, next_offset=offset + self.options.count, can_continue=bool(items), offset=offset)
        page = parse_getmsg(body)
        page.offset = offset
        return page

    def _next_offset(self, offset: int, page: HistoryPage) -> int | None:
        if self.options.offset_mode == "legacy":
            return offset + self.options.count
        if page.can_continue is False:
            return None
        if page.next_offset is None or page.next_offset == offset:
            return None
        return page.next_offset

    def _consume_page(self, page: HistoryPage, rows: list[CrawlRow]) -> bool:
        for article in flatten_messages(page.items):
            outcome = decide_article(
                article,
                original_only=self.options.original_only,
                date_mode=self.options.date_mode,
                start_date=self.options.start_date,
                end_date=self.options.end_date,
            )
            if outcome.action == "stop":
                rows.append(_row(article, "stop", outcome.reason, outcome.message, page.offset))
                return True
            if outcome.action == "skip":
                rows.append(_row(article, "skip", outcome.reason, outcome.message, page.offset))
                continue
            if self.options.min_reads is not None or self.options.fetch_stats:
                stats_row = self._apply_reads(article, page.offset)
                if stats_row is not None:
                    rows.append(stats_row)
                    continue
            if not self.options.download:
                rows.append(_row(article, "list", "", "", page.offset))
                continue
            rows.append(self._download(article, page.offset))
        return False

    def _apply_reads(self, article, offset: int) -> CrawlRow | None:
        try:
            form = build_stat_form(article.url)
            try:
                body = self.transport.post_form(STAT_URL, form, ARTICLE_UA)
            except TypeError:
                body = self.transport.post_form(STAT_URL, form)
            stats = parse_like_num(_json_dict(body))
        except Exception as exc:
            if self.options.min_reads is not None and self.options.strict_reads:
                return _row(article, "skip", "read_count_unavailable", f"parse_like_num error: {exc}", offset)
            return None
        if self.options.min_reads is not None and below_min_reads(stats.read_num, self.options.min_reads):
            return _row(article, "skip", "low_reads", f"{article.title} 阅读量为{stats.read_num}，跳过下载", offset)
        return None

    def _download(self, article, offset: int) -> CrawlRow:
        try:
            try:
                raw = self.transport.get_text(article.url, ARTICLE_UA)
            except TypeError:
                raw = self.transport.get_text(article.url)
            page, kind = select_article_html(raw)
        except (ArticleParseError, Exception) as exc:
            return _row(article, "skip", "fetch_failed", f"下载失败，链接 {exc}", offset)
        if self.options.out_dir is None:
            return _row(article, "download", kind, "", offset)
        written = write_article_files(self.options.out_dir, article, page, self.options.save_markdown)
        return _row(article, "download", kind, "", offset, saved_path=str(written[0]))

    def _save_page(self, offset: int, body: str) -> None:
        if not self.options.save_pages or self.options.out_dir is None:
            return
        folder = self.options.out_dir / "_pages"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"offset-{offset}.json").write_text(body, encoding="utf-8")


def _json_dict(body: str) -> dict:
    import json

    data = json.loads(body)
    if not isinstance(data, dict):
        raise ArticleParseError("parse_like_num error")
    return data


def _row(article, action: str, reason: str, message: str, offset: int, saved_path: str = "") -> CrawlRow:
    return CrawlRow(
        kind="article",
        action=action,
        reason=reason,
        title=article.title,
        url=article.url,
        source=article.source,
        published_at=article.published_at,
        offset=offset,
        message=message,
        saved_path=saved_path,
    )


def write_manifest(path: Path, rows: list[CrawlRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["kind", "action", "reason", "title", "url", "source", "published_at", "offset", "message", "saved_path"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: getattr(row, name) for name in fields})
