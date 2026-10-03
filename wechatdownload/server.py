from __future__ import annotations

import argparse
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from wechatdownload.app import App, default_data_root

INSTRUCTIONS = """
本机微信公众号历史下载。密钥只留在数据目录，工具结果不返回密钥。
第一次对一个公众号：prepare_account，把 confirmation_url 在已登录的微信电脑版里打开，然后 capture_session。
扫描不到时用 import_session_url 粘贴微信里复制出的链接。
长任务用 download_history 或 download_album，再用 job_status 查看。密钥失效时任务变为 needs_session，重新捕获后 resume_job。
历史列表使用修正解析。漏文对照用 diagnose_page。
""".strip()


def build_server(app: App | None = None, port: int = 4545) -> FastMCP:
    app = app or App()
    mcp = FastMCP(
        name="wechat-mp-download",
        instructions=INSTRUCTIONS,
        host="127.0.0.1",
        port=port,
        streamable_http_path="/mcp",
    )

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

    @mcp.tool(description="后台下载历史文章。max_pages 为 0 表示直到没有下一页。")
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
        save_pages: bool = True,
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

    @mcp.tool(description="列出合集或公众号主页里的文章，不走历史 getmsg。")
    def list_album(url: str, max_pages: int = 5) -> dict:
        return app.list_album(url, max_pages=max_pages)

    @mcp.tool(description="后台下载合集或主页文章。")
    def download_album(url: str, max_pages: int = 0, save_markdown: bool = False) -> dict:
        return app.download_album(url, max_pages=max_pages, save_markdown=save_markdown)

    @mcp.tool(description="查看后台任务。job_id 为空时返回最近一个任务。")
    def job_status(job_id: str = "") -> dict:
        return app.job_status(job_id)

    @mcp.tool(description="取消后台任务。")
    def job_cancel(job_id: str = "") -> dict:
        return app.job_cancel(job_id)

    @mcp.tool(description="密钥重新捕获后，从上次偏移继续 needs_session、cancelled 或 failed 的任务。")
    def resume_job(job_id: str) -> dict:
        return app.resume_job(job_id)

    @mcp.tool(description="返回任务清单的 csv 或 json 路径。")
    def export_manifest(job_id: str, export_format: str = "csv") -> dict:
        return app.export_manifest(job_id, export_format=export_format)

    @mcp.tool(description="对照一页 getmsg 响应的 4.6 切片解析和修正解析。path 必须位于数据目录。")
    def diagnose_page(path: str = "", body: str = "") -> dict:
        return app.diagnose_page(path=path, body=body)

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="微信公众号下载 MCP 服务")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--data", default="", help="会话和任务目录，默认是用户目录下的 .wechat-mp-download")
    parser.add_argument("--port", type=int, default=4545)
    args = parser.parse_args(argv)
    if args.port <= 0 or args.port > 65535:
        raise SystemExit("端口无效")
    root = Path(args.data) if args.data else default_data_root()
    server = build_server(App(root), port=args.port)
    server.run(args.transport)


if __name__ == "__main__":
    main()
