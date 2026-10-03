from __future__ import annotations

import csv
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from wechatdownload.album import crawl_album
from wechatdownload.article import extract_title, select_article_html, write_article_files
from wechatdownload.biz import extract_biz
from wechatdownload.client import CrawlOptions, HistoryCrawler, write_manifest
from wechatdownload.http import UrllibTransport
from wechatdownload.listing import diff_parsers
from wechatdownload.models import ArticleRef, CrawlRow
from wechatdownload.session import credentials_in_text, scan_credentials
from wechatdownload.store import SessionStore
from wechatdownload.urls import KEY_EXPIRED_TEXT, build_home_url, normalize_content_url

ALLOWED_HOST = "mp.weixin.qq.com"
MANUAL_STEP = "在已登录的微信电脑版中打开 confirmation_url，等公众号页面加载完成后再调用 capture_session。扫描不到时，把微信里复制出的链接交给 import_session_url。"


def default_data_root() -> Path:
    import os

    env = os.environ.get("WECHAT_MP_DATA", "").strip()
    if env:
        return Path(env)
    return Path.home() / ".wechat-mp-download"


def ensure_mp_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host != ALLOWED_HOST:
        raise ValueError("只允许 mp.weixin.qq.com")
    return url.strip()


def summarize_rows(rows: list[CrawlRow]) -> dict:
    titles = [row.title for row in rows if row.title][:20]
    stops = [{"reason": row.reason, "message": row.message, "offset": row.offset} for row in rows if row.action == "stop"]
    return {
        "listed": sum(1 for row in rows if row.action == "list"),
        "downloaded": sum(1 for row in rows if row.action == "download"),
        "skipped": sum(1 for row in rows if row.action == "skip"),
        "stopped": len(stops),
        "stops": stops,
        "titles": titles,
    }


@dataclass
class Job:
    id: str
    kind: str
    status: str
    biz: str
    options: dict = field(default_factory=dict)
    resume_offset: int | None = None
    manifest_path: str = ""
    summary: dict = field(default_factory=dict)
    error: str = ""
    created_at: str = ""
    done_urls: list[str] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {
            "ok": True,
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "biz": self.biz,
            "resume_offset": self.resume_offset,
            "manifest_path": self.manifest_path,
            "summary": self.summary,
            "error": self.error,
            "created_at": self.created_at,
        }


class App:
    def __init__(self, root: Path | None = None, transport=None, scan=None):
        self.root = root or default_data_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = SessionStore(self.root / "session.json")
        self.transport = transport or UrllibTransport()
        self.scan = scan or scan_credentials
        self.jobs: dict[str, Job] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def prepare_account(self, text: str) -> dict:
        raw = (text or "").strip()
        if not raw:
            return {"ok": False, "error": "请提供文章链接、公众号链接，或含 __biz 的文本"}
        try:
            if raw.startswith("http"):
                ensure_mp_url(raw)
            biz = extract_biz(raw)
            if not biz and raw.startswith("http"):
                biz = extract_biz(self._get(raw))
        except (ValueError, RuntimeError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if not biz:
            return {"ok": False, "error": "获取公众号id失败，请检查链接"}
        self.store.update(biz=biz, verified=False)
        confirmation = build_home_url(biz)
        return {
            "ok": True,
            "biz": biz,
            "confirmation_url": confirmation,
            "manual_step": MANUAL_STEP,
        }

    def capture_session(self, root: str = "") -> dict:
        pending = self.store.load()
        biz = str(pending.get("biz") or "")
        if not biz:
            return {"ok": False, "error": "还没有公众号 id。请先 prepare_account"}
        roots = [Path(root)] if root else None
        found = self.scan(roots)
        if not found:
            return {
                "ok": False,
                "error": "没有扫到密钥。请在微信中打开确认链接后再试，或使用 import_session_url",
                "scanned": 0,
            }
        last_error = "获取密钥失败...请先在微信打开复制的链接"
        for cred in found:
            try:
                self._verify(biz, cred.uin, cred.key, cred.pass_ticket, cred.poc_token)
            except RuntimeError as exc:
                last_error = str(exc)
                continue
            self._save_secret(biz, cred.uin, cred.key, cred.pass_ticket, cred.poc_token, source="scan")
            status = self.session_status()
            status.update({"ok": True, "scanned": len(found)})
            return status
        return {"ok": False, "error": last_error, "scanned": len(found)}

    def import_session_url(self, url: str) -> dict:
        try:
            cleaned = ensure_mp_url(url)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        found = credentials_in_text(cleaned)
        if not found:
            return {"ok": False, "error": "链接里没有 uin 和 key"}
        cred = found[0]
        biz = extract_biz(cleaned) or str(self.store.load().get("biz") or "")
        if not biz:
            return {"ok": False, "error": "链接里没有 __biz，请先 prepare_account"}
        try:
            self._verify(biz, cred.uin, cred.key, cred.pass_ticket, cred.poc_token)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        self._save_secret(biz, cred.uin, cred.key, cred.pass_ticket, cred.poc_token, source="import")
        status = self.session_status()
        status["ok"] = True
        return status

    def session_status(self) -> dict:
        status = self.store.public()
        status["ok"] = True
        return status

    def list_history(self, **options: object) -> dict:
        try:
            session = self.store.require()
            crawl_options, _ = self._crawl_options(options, download=False)
        except (RuntimeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if crawl_options.max_pages is None or crawl_options.max_pages > 30:
            return {"ok": False, "error": "list_history 最多 30 页。更长的历史请用 download_history"}
        folder = self.root / "lists" / datetime.now().strftime("%Y%m%d-%H%M%S")
        crawl_options.out_dir = folder
        rows = self._history(session, crawl_options)
        write_manifest(folder / "download_manifest.csv", rows)
        result = summarize_rows(rows)
        result.update({"ok": True, "manifest_path": str(folder / "download_manifest.csv")})
        if any(row.reason == "key_expired" for row in rows):
            result["ok"] = False
            result["error"] = KEY_EXPIRED_TEXT
        return result

    def download_history(self, **options: object) -> dict:
        return self._start_job("history", options)

    def download_album(self, url: str, **options: object) -> dict:
        try:
            ensure_mp_url(url)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        payload = dict(options)
        payload["url"] = url
        return self._start_job("album", payload)

    def list_album(self, url: str, max_pages: int = 5) -> dict:
        try:
            ensure_mp_url(url)
            session = self.store.require()
            articles = crawl_album(
                url,
                biz=str(session["biz"]),
                uin=str(session["uin"]),
                key=str(session["key"]),
                pass_ticket=str(session.get("pass_ticket") or ""),
                get_text=self._get,
                max_pages=max_pages,
            )
        except (RuntimeError, ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        folder = self.root / "albums" / datetime.now().strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "album.json"
        path.write_text(
            json.dumps(
                [{"title": item.title, "url": item.url, "msg_id": item.msg_id, "item_idx": item.item_idx} for item in articles],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "count": len(articles),
            "titles": [item.title for item in articles][:20],
            "manifest_path": str(path),
        }

    def download_one(self, url: str) -> dict:
        try:
            cleaned = ensure_mp_url(url)
            session = self.store.require()
            page, kind = select_article_html(self._get(cleaned))
        except (RuntimeError, ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if KEY_EXPIRED_TEXT in page and "js_content" not in page and "cdn_url" not in page:
            return {"ok": False, "error": KEY_EXPIRED_TEXT}
        article = ArticleRef(
            title=extract_title(page) or "untitled",
            url=normalize_content_url(cleaned),
            published_at=None,
            copyright_stat=None,
            copyright_type=None,
            source="single",
        )
        folder = self.root / "articles" / str(session["biz"])
        written = write_article_files(folder, article, page, save_markdown=False)
        return {"ok": True, "title": article.title, "kind": kind, "saved_path": str(written[0])}

    def job_status(self, job_id: str = "") -> dict:
        job = self._job(job_id)
        if job is None:
            return {"ok": False, "error": "没有任务"}
        return job.public()

    def job_cancel(self, job_id: str = "") -> dict:
        job = self._job(job_id)
        if job is None:
            return {"ok": False, "error": "没有任务"}
        job.cancel_event.set()
        return {"ok": True, "job_id": job.id, "status": job.status}

    def resume_job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            return {"ok": False, "error": "没有任务"}
        if job.status not in {"needs_session", "cancelled", "failed"}:
            return {"ok": False, "error": f"任务状态是 {job.status}，不能恢复"}
        try:
            self.store.require()
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        if self._busy():
            return {"ok": False, "error": "已有任务在跑", "job_id": self._running_id()}
        job.status = "queued"
        job.error = ""
        job.cancel_event = threading.Event()
        self._spawn(job)
        return job.public()

    def export_manifest(self, job_id: str, export_format: str = "csv") -> dict:
        job = self.jobs.get(job_id)
        if job is None or not job.manifest_path:
            return {"ok": False, "error": "任务还没有清单"}
        source = Path(job.manifest_path)
        if not source.is_file():
            return {"ok": False, "error": "清单文件不存在"}
        if export_format == "csv":
            return {"ok": True, "path": str(source)}
        if export_format != "json":
            return {"ok": False, "error": "format 只能是 csv 或 json"}
        with source.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        target = source.with_suffix(".json")
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(target)}

    def diagnose_page(self, path: str = "", body: str = "") -> dict:
        if body:
            if len(body) > 2_000_000:
                return {"ok": False, "error": "响应正文过长"}
            text = body
        elif path:
            file_path = Path(path).resolve()
            root = self.root.resolve()
            if root not in file_path.parents and file_path != root:
                return {"ok": False, "error": "只能读取数据目录里的页文件"}
            try:
                text = file_path.read_text(encoding="utf-8")
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
        else:
            return {"ok": False, "error": "提供 path 或 body"}
        diff = diff_parsers(text)
        return {
            "ok": diff.fixed_error is None,
            "legacy_error": diff.legacy_error or "",
            "fixed_error": diff.fixed_error or "",
            "legacy_count": len(diff.legacy_ids),
            "fixed_count": len(diff.fixed_ids),
            "only_fixed_count": len(diff.only_fixed),
            "only_fixed": diff.only_fixed[:20],
            "next_offset": diff.next_offset,
            "can_continue": diff.can_continue,
        }

    def wait_job(self, job_id: str, timeout: float = 5) -> None:
        thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)

    def _start_job(self, kind: str, options: dict) -> dict:
        try:
            session = self.store.require()
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        if self._busy():
            return {"ok": False, "error": "已有任务在跑", "job_id": self._running_id()}
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            status="queued",
            biz=str(session["biz"]),
            options={key: value for key, value in options.items() if key != "cancel_event"},
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self.jobs[job.id] = job
        self._spawn(job)
        return job.public()

    def _spawn(self, job: Job) -> None:
        thread = threading.Thread(target=self._execute, args=(job,), name=f"wechat-mp-{job.id}", daemon=True)
        self._threads[job.id] = thread
        thread.start()

    def _execute(self, job: Job) -> None:
        job.status = "running"
        folder = self.root / "jobs" / job.id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            session = self.store.require()
            if job.kind == "history":
                options, explicit_offset = self._crawl_options(job.options, download=True)
                options.out_dir = folder
                options.cancel_event = job.cancel_event
                if job.resume_offset is not None:
                    options.start_offset = job.resume_offset
                elif explicit_offset is not None:
                    options.start_offset = explicit_offset
                rows = self._history(session, options)
            elif job.kind == "album":
                rows = self._album_rows(session, job, folder)
            else:
                raise RuntimeError(f"未知任务 {job.kind}")
            manifest = folder / "download_manifest.csv"
            write_manifest(manifest, rows)
            job.manifest_path = str(manifest)
            job.summary = summarize_rows(rows)
            job.done_urls = [row.url for row in rows if row.action == "download" and row.url]
            stop = next((row for row in rows if row.reason == "key_expired"), None)
            if stop is not None:
                job.status = "needs_session"
                job.resume_offset = stop.offset
                job.error = KEY_EXPIRED_TEXT
            elif any(row.reason == "cancelled" for row in rows):
                job.status = "cancelled"
                cancelled = next(row for row in rows if row.reason == "cancelled")
                job.resume_offset = cancelled.offset
            elif any(row.reason in {"request_failed", "api_ret", "parse", "legacy_parse"} for row in rows):
                job.status = "failed"
                failed = next(row for row in rows if row.action == "stop")
                job.error = failed.message or failed.reason
                job.resume_offset = failed.offset
            else:
                job.status = "done"
                job.resume_offset = None
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        self._persist_job(job)

    def _album_rows(self, session: dict, job: Job, folder: Path) -> list[CrawlRow]:
        url = str(job.options.get("url") or "")
        ensure_mp_url(url)
        articles = crawl_album(
            url,
            biz=str(session["biz"]),
            uin=str(session["uin"]),
            key=str(session["key"]),
            pass_ticket=str(session.get("pass_ticket") or ""),
            get_text=self._get,
            max_pages=int(job.options.get("max_pages") or 0) or None,
        )
        rows: list[CrawlRow] = []
        seen = set(job.done_urls)
        for article in articles:
            if job.cancel_event.is_set():
                rows.append(CrawlRow("page", "stop", "cancelled", message="任务已取消"))
                break
            if not article.url or article.url in seen:
                continue
            try:
                raw = self._get(article.url)
            except (RuntimeError, OSError) as exc:
                rows.append(CrawlRow("article", "skip", "fetch_failed", title=article.title, url=article.url, message=str(exc)))
                continue
            if KEY_EXPIRED_TEXT in raw and "general_msg_list" not in raw and "js_content" not in raw:
                rows.append(CrawlRow("article", "stop", "key_expired", title=article.title, url=article.url, message=KEY_EXPIRED_TEXT))
                break
            page, kind = select_article_html(raw)
            written = write_article_files(folder, article, page, save_markdown=bool(job.options.get("save_markdown")))
            rows.append(
                CrawlRow(
                    "article",
                    "download",
                    kind,
                    title=article.title,
                    url=article.url,
                    source="album",
                    saved_path=str(written[0]),
                )
            )
        return rows

    def _history(self, session: dict, options: CrawlOptions) -> list[CrawlRow]:
        return HistoryCrawler(
            str(session["biz"]),
            str(session["uin"]),
            str(session["key"]),
            str(session.get("pass_ticket") or ""),
            self.transport,
            options,
            poc_token=str(session.get("poc_token") or ""),
        ).run()

    def _crawl_options(self, options: dict, download: bool) -> tuple[CrawlOptions, int | None]:
        max_pages = options.get("max_pages", 3 if not download else None)
        if max_pages in ("", None):
            parsed_pages = None
        else:
            parsed_pages = int(max_pages)
            if parsed_pages <= 0:
                parsed_pages = None
        min_reads = options.get("min_reads")
        parsed_reads = int(min_reads) if min_reads not in (None, "", 0) else None
        start_offset = options.get("start_offset")
        explicit = int(start_offset) if start_offset not in (None, "") else None
        crawl = CrawlOptions(
            parser="fixed",
            offset_mode="server",
            start_page=int(options.get("start_page") or 1),
            max_pages=parsed_pages,
            delay_seconds=float(options.get("delay_seconds") or 0),
            original_only=bool(options.get("original_only")),
            date_mode=str(options.get("date_mode") or "all"),
            start_date=_date(str(options.get("start_date") or "")),
            end_date=_date(str(options.get("end_date") or "")),
            min_reads=parsed_reads,
            download=download,
            save_markdown=bool(options.get("save_markdown")),
            save_pages=bool(options.get("save_pages")),
            start_offset=explicit,
        )
        if crawl.date_mode not in {"all", "skip", "stop"}:
            raise ValueError("date_mode 只能是 all、skip 或 stop")
        return crawl, explicit

    def _verify(self, biz: str, uin: str, key: str, pass_ticket: str, poc_token: str) -> None:
        body = self._get(build_home_url(biz, uin, key, pass_ticket, poc_token))
        if KEY_EXPIRED_TEXT in body:
            raise RuntimeError("获取密钥失败...请先在微信打开复制的链接")

    def _save_secret(self, biz: str, uin: str, key: str, pass_ticket: str, poc_token: str, source: str) -> None:
        self.store.update(
            biz=biz,
            uin=uin,
            key=key,
            pass_ticket=pass_ticket,
            poc_token=poc_token,
            verified=True,
            verified_at=time.time(),
            source=source,
        )

    def _get(self, url: str, user_agent: str | None = None) -> str:
        if user_agent is None:
            try:
                return self.transport.get_text(url)
            except TypeError:
                return self.transport.get_text(url, None)
        try:
            return self.transport.get_text(url, user_agent)
        except TypeError:
            return self.transport.get_text(url)

    def _job(self, job_id: str) -> Job | None:
        if job_id:
            return self.jobs.get(job_id)
        if not self.jobs:
            return None
        return list(self.jobs.values())[-1]

    def _busy(self) -> bool:
        return any(job.status in {"queued", "running"} for job in self.jobs.values())

    def _running_id(self) -> str:
        for job in self.jobs.values():
            if job.status in {"queued", "running"}:
                return job.id
        return ""

    def _persist_job(self, job: Job) -> None:
        path = self.root / "jobs" / job.id / "job.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job.public(), ensure_ascii=False, indent=2), encoding="utf-8")


def _date(value: str):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()
