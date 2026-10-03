from __future__ import annotations

import argparse
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wechatdownload.app import App, default_data_root

AGENT_INSTRUCTIONS = (
    "微信公众号下载。单篇和公开合集不需要会话；整号历史要先在微信电脑版打开确认链接。"
    "结果只有标题、路径和短摘录，不含页面 HTML 或密钥。启动时不扫描微信，也不访问网络。"
)
AGENT_BODY_LIMIT = 20_000
FULL_NAMES = (
    "prepare_account",
    "capture_session",
    "import_session_url",
    "session_status",
    "list_history",
    "download_history",
    "download_one",
    "list_album",
    "download_album",
    "job_status",
    "job_cancel",
    "resume_job",
    "export_manifest",
    "diagnose_page",
)
AGENT_NAMES = ("mp_session", "mp_fetch", "mp_history", "mp_job", "mp_diagnose")


def resolve_toolset(explicit: str | None = None) -> str:
    raw = os.environ.get("WECHAT_MP_TOOLSET", "agent") if explicit is None else explicit
    name = (raw or "agent").strip().lower()
    if name in {"agent", "default"}:
        return "agent"
    if name == "full":
        return "full"
    raise ValueError("工具面只能是 agent 或 full")


def build_server(app: App | None = None, port: int = 4545, toolset: str | None = None) -> FastMCP:
    app = app or App()
    selected = resolve_toolset(toolset)
    instructions = AGENT_INSTRUCTIONS if selected == "agent" else AGENT_INSTRUCTIONS + " 当前是完整工具面。"
    mcp = FastMCP(
        name="wechat-mp-download-mcp",
        instructions=instructions,
        host="127.0.0.1",
        port=port,
        streamable_http_path="/mcp",
    )
    if selected == "agent":
        _register_agent(mcp, app)
    else:
        _register_full(mcp, app)
    return mcp


def _register_agent(mcp: FastMCP, app: App) -> None:
    @mcp.tool(description="会话。action 为 status、prepare、capture 或 import。text 是文章链接或微信里复制的链接。")
    def mp_session(action: str = "status", text: str = "", root: str = "") -> dict:
        selected = (action or "status").strip().lower()
        if selected == "status":
            return app.session_status()
        if selected == "prepare":
            return app.prepare_account(text)
        if selected == "capture":
            return app.capture_session(root)
        if selected == "import":
            return app.import_session_url(text)
        return {"ok": False, "error": "action 只能是 status、prepare、capture 或 import"}

    @mcp.tool(description="单篇或公开合集。mode 为 info、save 或 list。只返回标题、路径和短摘录。")
    def mp_fetch(url: str, mode: str = "info", excerpt_chars: int = 600) -> dict:
        return app.fetch(url, mode=mode, excerpt_chars=excerpt_chars)

    @mcp.tool(description="历史。action 为 list 或 download。list 默认 3 页；download 在 max_pages 小于 0 时翻到结束，不保存原始页。")
    def mp_history(
        action: str = "list",
        max_pages: int = -1,
        original_only: bool = False,
        date_mode: str = "all",
        start_date: str = "",
        end_date: str = "",
        min_reads: int = 0,
    ) -> dict:
        selected = (action or "list").strip().lower()
        if selected == "list":
            return app.list_history(
                max_pages=3 if max_pages < 0 else max_pages,
                original_only=original_only,
                date_mode=date_mode,
                start_date=start_date,
                end_date=end_date,
                min_reads=min_reads,
            )
        if selected == "download":
            return app.download_history(
                max_pages=0 if max_pages < 0 else max_pages,
                original_only=original_only,
                date_mode=date_mode,
                start_date=start_date,
                end_date=end_date,
                min_reads=min_reads,
                delay_seconds=1,
                save_markdown=False,
                save_pages=False,
            )
        return {"ok": False, "error": "action 只能是 list 或 download"}

    @mcp.tool(description="后台任务。action 为 status、cancel、resume 或 export。")
    def mp_job(action: str = "status", job_id: str = "", export_format: str = "csv") -> dict:
        selected = (action or "status").strip().lower()
        if selected == "status":
            return app.job_status(job_id)
        if selected == "cancel":
            return app.job_cancel(job_id)
        if selected == "resume":
            return app.resume_job(job_id)
        if selected == "export":
            return app.export_manifest(job_id, export_format=export_format)
        return {"ok": False, "error": "action 只能是 status、cancel、resume 或 export"}

    @mcp.tool(description="对照一页 getmsg 的旧切片和修正解析。path 必须在数据目录内。")
    def mp_diagnose(path: str = "", body: str = "") -> dict:
        if len(body) > AGENT_BODY_LIMIT:
            return {"ok": False, "error": "body 过长。请把响应留在数据目录，再用 path 读取"}
        return app.diagnose_page(path=path, body=body)


def _register_full(mcp: FastMCP, app: App) -> None:
    @mcp.tool(description="从文章或公众号链接提取 biz，并返回要在微信里打开的确认链接。")
    def prepare_account(text: str) -> dict:
        return app.prepare_account(text)

    @mcp.tool(description="扫描本机微信目录并校验密钥。结果不含密钥。")
    def capture_session(root: str = "") -> dict:
        return app.capture_session(root)

    @mcp.tool(description="从微信里复制出的 mp.weixin.qq.com 链接导入 uin 和 key。")
    def import_session_url(url: str) -> dict:
        return app.import_session_url(url)

    @mcp.tool(description="查看会话是否有效。不返回密钥。")
    def session_status() -> dict:
        return app.session_status()

    @mcp.tool(description="同步列出最多 30 页历史。更长的历史用 download_history。")
    def list_history(
        max_pages: int = 3,
        start_page: int = 1,
        original_only: bool = False,
        date_mode: str = "all",
        start_date: str = "",
        end_date: str = "",
        min_reads: int = 0,
        delay_seconds: float = 0,
    ) -> dict:
        return app.list_history(
            max_pages=max_pages,
            start_page=start_page,
            original_only=original_only,
            date_mode=date_mode,
            start_date=start_date,
            end_date=end_date,
            min_reads=min_reads,
            delay_seconds=delay_seconds,
        )

    @mcp.tool(description="后台下载历史文章。max_pages 为 0 表示直到没有下一页。默认不保存原始页。")
    def download_history(
        max_pages: int = 0,
        start_page: int = 1,
        start_offset: int = -1,
        original_only: bool = False,
        date_mode: str = "all",
        start_date: str = "",
        end_date: str = "",
        min_reads: int = 0,
        delay_seconds: float = 1,
        save_markdown: bool = False,
        save_pages: bool = False,
    ) -> dict:
        return app.download_history(
            max_pages=max_pages,
            start_page=start_page,
            start_offset=None if start_offset < 0 else start_offset,
            original_only=original_only,
            date_mode=date_mode,
            start_date=start_date,
            end_date=end_date,
            min_reads=min_reads,
            delay_seconds=delay_seconds,
            save_markdown=save_markdown,
            save_pages=save_pages,
        )

    @mcp.tool(description="下载单篇 mp.weixin.qq.com 文章 HTML。")
    def download_one(url: str) -> dict:
        return app.download_one(url)

    @mcp.tool(description="列出合集或公众号主页里的文章。公开合集不需要会话。")
    def list_album(url: str, max_pages: int = 5) -> dict:
        return app.list_album(url, max_pages=max_pages)

    @mcp.tool(description="后台下载合集或主页文章。公开合集不需要会话。")
    def download_album(url: str, max_pages: int = 0, save_markdown: bool = False) -> dict:
        return app.download_album(url, max_pages=max_pages, save_markdown=save_markdown)

    @mcp.tool(description="查看后台任务。job_id 为空时返回最近一个任务。")
    def job_status(job_id: str = "") -> dict:
        return app.job_status(job_id)

    @mcp.tool(description="取消后台任务。")
    def job_cancel(job_id: str = "") -> dict:
        return app.job_cancel(job_id)

    @mcp.tool(description="从上次偏移继续 needs_session、cancelled 或 failed 的任务。")
    def resume_job(job_id: str) -> dict:
        return app.resume_job(job_id)

    @mcp.tool(description="返回任务清单的 csv 或 json 路径。")
    def export_manifest(job_id: str, export_format: str = "csv") -> dict:
        return app.export_manifest(job_id, export_format=export_format)

    @mcp.tool(description="对照一页 getmsg 响应的 4.6 切片解析和修正解析。path 必须位于数据目录。")
    def diagnose_page(path: str = "", body: str = "") -> dict:
        return app.diagnose_page(path=path, body=body)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="微信公众号下载 MCP 服务")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--data", default="", help="会话和任务目录，默认是用户目录下的 .wechat-mp-download")
    parser.add_argument("--port", type=int, default=4545)
    parser.add_argument("--toolset", choices=("agent", "full"), default="", help="默认 agent，或用环境变量 WECHAT_MP_TOOLSET")
    args = parser.parse_args(argv)
    if args.port <= 0 or args.port > 65535:
        raise SystemExit("端口无效")
    root = Path(args.data) if args.data else default_data_root()
    server = build_server(App(root), port=args.port, toolset=args.toolset or None)
    server.run(args.transport)


if __name__ == "__main__":
    main()
