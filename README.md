# wechat-mp-download

本机微信公众号历史文章下载器。没有界面。MCP 负责会话、列表、下载和漏文对照。

下载行为来自本机 4.6 工具的接口和字段。历史列表默认使用修正解析：完整读取 getmsg JSON，并跟随服务端 `next_offset`。4.6 那种 `general_msg_list":"` 到 `","next_offset` 的切片只留在 `diagnose_page` 里做对照。

密钥留在数据目录的 `session.json`。工具返回里只有是否有效和 uin 末四位。

## 手动步骤

微信不会把历史接口的密钥交给一个没打开过页面的程序。每个公众号、每次密钥失效，都要在已经登录的微信电脑版里做一次：

1. `prepare_account` 得到 `confirmation_url`。
2. 在微信里打开它，等公众号页面加载完。
3. `capture_session`。如果本机目录里扫不到，把微信中复制出的链接交给 `import_session_url`。

之后列表、下载和合集不再需要界面。任务变成 `needs_session` 时，重复第 2 步和第 3 步，再 `resume_job`。

## MCP

stdio 是主入口。在这个目录启动：

```text
D:\reverse_ENV\.venv\Scripts\python.exe -m wechatdownload.server
```

客户端配置：

```json
{
  "mcpServers": {
    "wechat-mp-download": {
      "command": "D:\\reverse_ENV\\.venv\\Scripts\\python.exe",
      "args": ["-m", "wechatdownload.server"],
      "cwd": "D:\\reverse_ENV\\workspace\\wechat-mp-download"
    }
  }
}
```

数据目录默认是用户主目录下的 `.wechat-mp-download`。可以用环境变量 `WECHAT_MP_DATA` 或参数 `--data` 改掉。

兼容旧的本机 HTTP 配置时，只监听环回地址：

```text
python -m wechatdownload.server --transport streamable-http --port 4545
```

地址是 `http://127.0.0.1:4545/mcp`。

## 工具

| 工具 | 作用 |
| --- | --- |
| `prepare_account` | 提取 `__biz`，返回确认链接 |
| `capture_session` | 扫描本机微信目录并校验 |
| `import_session_url` | 从微信复制出的链接导入会话 |
| `session_status` | 会话是否有效 |
| `list_history` | 同步列出最多 30 页 |
| `download_history` | 后台下载，`max_pages` 为 0 时一直翻到结束 |
| `download_one` | 下载单篇 HTML |
| `list_album` / `download_album` | 合集和主页 |
| `job_status` / `job_cancel` / `resume_job` | 任务进度、取消、从偏移恢复 |
| `export_manifest` | 清单的 csv 或 json 路径 |
| `diagnose_page` | 对照切片解析和修正解析 |

清单里的跳过原因包括 `empty_content_url`、`not_original`、`low_reads`、`before_start_date`。整页失败会记 `key_expired` 或 `legacy_parse`。

命令行仍然可以做同一件事：`python -m wechatdownload --help`。

## 开发

```text
D:\reverse_ENV\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

依赖是 Python 3.10 以上和 `mcp`。安装：`pip install -e .`

## 仓库范围

这个目录是独立仓库。解包出的 exe、dll、常量转储和会话文件不进 Git。主仓 `reverse_ENV` 的工作区当时还有别的未提交改动，所以这次没有改它的项目登记。
