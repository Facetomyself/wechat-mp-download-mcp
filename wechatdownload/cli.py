from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from wechatdownload import __version__
from wechatdownload.album import AlbumError, crawl_album
from wechatdownload.biz import extract_biz, extract_biz_legacy
from wechatdownload.client import CrawlOptions, HistoryCrawler, write_manifest
from wechatdownload.http import UrllibTransport
from wechatdownload.listing import diff_parsers
from wechatdownload.session import default_roots, scan_credentials
from wechatdownload.urls import KEY_EXPIRED_TEXT, build_home_url


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (AlbumError, ValueError, OSError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wechatdownload",
        description="微信公众号文章下载 4.6 行为重构。默认使用修正后的历史列表解析。",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    biz = sub.add_parser("biz", help="从文章链接或页面文本提取 __biz")
    biz.add_argument("text")
    biz.add_argument("--legacy", action="store_true", help="只用 4.6 的 __biz=(.*?)& 正则")
    biz.set_defaults(func=cmd_biz)

    keys = sub.add_parser("keys", help="从本机微信目录扫描 uin/key，写入本地文件")
    keys.add_argument("--root", action="append", default=[], help="额外扫描目录，可重复")
    keys.add_argument("--out", default="keys.local.json")
    keys.add_argument("--limit", type=int, default=20)
    keys.set_defaults(func=cmd_keys)

    verify = sub.add_parser("verify", help="用 profile_ext 首页判断密钥是否失效")
    _add_credential_args(verify)
    verify.set_defaults(func=cmd_verify)

    diagnose = sub.add_parser("diagnose", help="对照一份 getmsg 响应的两种解析结果")
    diagnose.add_argument("file")
    diagnose.set_defaults(func=cmd_diagnose)

    list_cmd = sub.add_parser("list", help="只列历史文章，不下载正文")
    _add_credential_args(list_cmd)
    _add_crawl_args(list_cmd)
    list_cmd.set_defaults(func=cmd_list, download=False)

    download = sub.add_parser("download", help="下载历史文章 HTML")
    _add_credential_args(download)
    _add_crawl_args(download)
    download.add_argument("--markdown", action="store_true")
    download.add_argument("--save-pages", action="store_true", help="保存每一页 getmsg 原文，方便再 diagnose")
    download.set_defaults(func=cmd_list, download=True)

    album = sub.add_parser("album", help="下载合集或主页里的文章列表")
    _add_credential_args(album)
    album.add_argument("--url", required=True)
    album.add_argument("--max-pages", type=int, default=None)
    album.add_argument("--out", default="")
    album.set_defaults(func=cmd_album)
    return parser


def _add_credential_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--credential", default="", help="keys.local.json 里的一条，或含 biz/uin/key/pass_ticket 的文件")
    parser.add_argument("--biz", default="")
    parser.add_argument("--uin", default="")
    parser.add_argument("--key", default="")
    parser.add_argument("--pass-ticket", default="")
    parser.add_argument("--poc-token", default="")
    parser.add_argument("--cookie", default="")
    parser.add_argument("--proxy", default="")


def _add_crawl_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--parser", choices=("fixed", "legacy"), default="fixed")
    parser.add_argument("--offset", choices=("server", "legacy"), default="server")
    parser.add_argument("--start-page", type=int, default=1, help="起始下载页数，第一页偏移是 (页数-1)*10")
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0, help="每页暂停秒数")
    parser.add_argument("--original-only", action="store_true", help="对应 CheckBox_11，跳过非原创")
    parser.add_argument("--date-mode", choices=("all", "skip", "stop"), default="all")
    parser.add_argument("--start-date", default="", help="YYYY-MM-DD")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--min-reads", type=int, default=None, help="对应阅读量输入框，低于此值跳过")
    parser.add_argument("--strict-reads", action="store_true", help="阅读量接口失败时也跳过")
    parser.add_argument("--stats", action="store_true", help="请求 getappmsgext，不设 --min-reads 时不因此跳过")
    parser.add_argument("--out", default="")


def cmd_biz(args: argparse.Namespace) -> int:
    text = args.text
    if Path(text).is_file():
        text = Path(text).read_text(encoding="utf-8", errors="replace")
    found = extract_biz_legacy(text) if args.legacy else extract_biz(text)
    if not found:
        print("获取公众号id失败，请检查链接或者网络是否正常", file=sys.stderr)
        return 1
    print(found)
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    roots = [Path(item) for item in args.root] if args.root else default_roots()
    found = scan_credentials(roots, limit=args.limit)
    payload = [
        {
            "uin": item.uin,
            "key": item.key,
            "pass_ticket": item.pass_ticket,
            "poc_token": item.poc_token,
            "source": item.source,
            "mtime": item.mtime,
        }
        for item in found
    ]
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"写入 {out} ，{len(payload)} 条。终端不打印密钥。")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cred = _credential(args)
    transport = UrllibTransport(cookie=cred["cookie"], proxy=args.proxy)
    url = build_home_url(cred["biz"], cred["uin"], cred["key"], cred["pass_ticket"], cred["poc_token"])
    body = transport.get_text(url)
    if KEY_EXPIRED_TEXT in body:
        print("获取密钥失败...请先在微信打开复制的链接")
        return 1
    print("获取密钥成功！请点击下载按钮")
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    body = Path(args.file).read_text(encoding="utf-8")
    diff = diff_parsers(body)
    print(f"legacy_error: {diff.legacy_error or ''}")
    print(f"fixed_error: {diff.fixed_error or ''}")
    print(f"legacy_count: {len(diff.legacy_ids)}")
    print(f"fixed_count: {len(diff.fixed_ids)}")
    print(f"next_offset: {'' if diff.next_offset is None else diff.next_offset}")
    print(f"can_continue: {'' if diff.can_continue is None else diff.can_continue}")
    print(f"only_fixed: {len(diff.only_fixed)}")
    for item in diff.only_fixed:
        print(f"  + {item}")
    if diff.only_legacy:
        print(f"only_legacy: {len(diff.only_legacy)}")
        for item in diff.only_legacy:
            print(f"  - {item}")
    return 0 if diff.fixed_error is None else 1


def cmd_list(args: argparse.Namespace) -> int:
    cred = _credential(args)
    options = _options(args)
    transport = UrllibTransport(cookie=cred["cookie"], proxy=args.proxy)
    rows = HistoryCrawler(
        cred["biz"],
        cred["uin"],
        cred["key"],
        cred["pass_ticket"],
        transport,
        options,
        poc_token=cred["poc_token"],
    ).run()
    downloaded = sum(1 for row in rows if row.action == "download")
    listed = sum(1 for row in rows if row.action == "list")
    skipped = sum(1 for row in rows if row.action == "skip")
    stopped = [row for row in rows if row.action == "stop"]
    print(f"列出 {listed}，下载 {downloaded}，跳过 {skipped}，停止 {len(stopped)}")
    for row in stopped:
        print(f"停止: {row.reason} {row.message}")
    if options.out_dir is not None:
        print(f"清单: {options.out_dir / 'download_manifest.csv'}")
    elif rows:
        write_manifest(Path("download_manifest.csv"), rows)
        print("清单: download_manifest.csv")
    return 0 if not any(row.reason in {"request_failed", "api_ret", "legacy_parse", "parse"} for row in stopped) else 2


def cmd_album(args: argparse.Namespace) -> int:
    cred = _credential(args)
    transport = UrllibTransport(cookie=cred["cookie"], proxy=args.proxy)
    articles = crawl_album(
        args.url,
        biz=cred["biz"],
        uin=cred["uin"],
        key=cred["key"],
        pass_ticket=cred["pass_ticket"],
        get_text=transport.get_text,
        max_pages=args.max_pages,
    )
    print(f"合集文章 {len(articles)}")
    for article in articles:
        print(f"{article.title}\t{article.url}")
    if args.out:
        path = Path(args.out)
        path.write_text(
            json.dumps([article.__dict__ for article in articles], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"写入 {path}")
    return 0


def _credential(args: argparse.Namespace) -> dict[str, str]:
    data: dict[str, str] = {}
    if args.credential:
        loaded = json.loads(Path(args.credential).read_text(encoding="utf-8"))
        if isinstance(loaded, list):
            if not loaded:
                raise ValueError("credential 文件是空列表")
            loaded = loaded[0]
        if not isinstance(loaded, dict):
            raise ValueError("credential 文件格式不对")
        data = {key: "" if value is None else str(value) for key, value in loaded.items()}
    cred = {
        "biz": args.biz or data.get("biz", ""),
        "uin": args.uin or data.get("uin", ""),
        "key": args.key or data.get("key", ""),
        "pass_ticket": args.pass_ticket or data.get("pass_ticket", ""),
        "poc_token": args.poc_token or data.get("poc_token", ""),
        "cookie": args.cookie or data.get("cookie", ""),
    }
    missing = [name for name in ("biz", "uin", "key") if not cred[name]]
    if missing:
        raise ValueError("缺少 " + ", ".join(missing))
    return cred


def _options(args: argparse.Namespace) -> CrawlOptions:
    out = Path(args.out) if args.out else None
    return CrawlOptions(
        parser=args.parser,
        offset_mode=args.offset,
        start_page=args.start_page,
        max_pages=args.max_pages,
        delay_seconds=args.delay,
        original_only=args.original_only,
        date_mode=args.date_mode,
        start_date=_date(args.start_date),
        end_date=_date(args.end_date),
        min_reads=args.min_reads,
        strict_reads=args.strict_reads,
        fetch_stats=args.stats,
        download=args.download,
        save_markdown=getattr(args, "markdown", False),
        save_pages=getattr(args, "save_pages", False),
        out_dir=out,
    )


def _date(value: str) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()
