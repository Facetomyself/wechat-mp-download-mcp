# wechat-mp-download

本机微信公众号文章下载器，版本 0.1.0。没有界面。MCP 是主入口，命令行调用同一套下载库。

历史列表默认完整读取 `getmsg` JSON，并跟随服务端 `next_offset`。4.6 那种从 `general_msg_list":"` 切到 `","next_offset` 的解析只留在 `diagnose_page` 里做对照。请求主机只允许 `mp.weixin.qq.com`。

`uin`、`key`、`pass_ticket` 只写在数据目录的 `session.json`。工具结果里最多出现 uin 末四位，不返回密钥本身。

## 能力

| 操作 | 要不要已校验会话 | 入口 |
| --- | --- | --- |
| 下载一篇公开文章的 HTML | 不要。页面要求在微信内打开时停下 | MCP `download_one` |
| 列出或下载一个公众号的历史 | 要。先在微信电脑版打开确认页 | MCP `list_history` / `download_history`，或 CLI `list` / `download` |
| 列出或下载合集、主页 | MCP 和 CLI 当前都要已校验会话 | MCP `list_album` / `download_album`，或 CLI `album` |
| 对照一页历史响应为什么少文 | 不要会话，要有那一页的响应正文 | MCP `diagnose_page`，或 CLI `diagnose` |

同一时间只跑一个后台任务。`download_history` 和 `download_album` 立刻返回 `job_id`，再用 `job_status` 看进度。

本仓库不做这些事：

- 不在本地计算 `uin` 或 `key`。
- 不访问 `mp.weixin.qq.com` 以外的主机。
- 不把代理默认设到 `127.0.0.1:7890`。MCP 工具没有代理参数；只有 CLI 的 `--proxy`。
- 不调用 4.6 常量里的版本检查地址。
- 不生成 PDF 或 docx。那两项在原工具里是外部程序。

## 安装

Python 3.10 或更高。下载、列表和对照解析只用标准库。MCP 服务需要 `mcp>=1.28`。Markdown 可选：安装了 `html2text` 就用它，否则去掉标签。

```text
pip install -e .
```

在 reverse_ENV 里不必再安装一份。项目虚拟环境已经有 `mcp`，把本目录放进 `PYTHONPATH` 即可：

```text
D:\reverse_ENV\.venv\Scripts\python.exe -m wechatdownload.server
```

脚本入口是 `wechat-mp-download` 和 `wechat-mp-mcp`。未安装到环境里时，用 `python -m wechatdownload` 和 `python -m wechatdownload.server`。

## 数据目录

默认是用户主目录下的 `.wechat-mp-download`。环境变量 `WECHAT_MP_DATA` 或服务参数 `--data` 可以改掉。这个目录不进 Git。

```text
.wechat-mp-download/
  session.json                 会话。含密钥，工具结果不读出原文
  articles/<biz>/              download_one 保存的 HTML
  jobs/<job_id>/
    download_manifest.csv      这次任务的清单
    download_manifest.json     export_manifest 选 json 时生成
    _pages/offset-<n>.json     download_history 默认保存的 getmsg 原文
    *.html                     历史或合集任务写下的正文
  lists/<时间>/                list_history 的清单
  albums/<时间>/album.json     list_album 的标题和链接
```

单篇文件名是 `发布时间_标题.html`。单篇接口没有发布时间时，前缀是 `unknown-time`。标题里的冒号等符号会被去掉。同名文件追加 `_2`、`_3`。

## 接入 MCP

stdio 是正式入口。reverse_ENV 把本仓库固定成 Private submodule `mcp/wechat-mp-download`，按需启用，不进冷启动。项目目录里的配置是：

```json
{
  "mcpServers": {
    "wechat-mp-download": {
      "command": "D:\\reverse_ENV\\.venv\\Scripts\\python.exe",
      "args": ["-m", "wechatdownload.server"],
      "env": {
        "PYTHONPATH": "D:\\reverse_ENV\\mcp\\wechat-mp-download"
      }
    }
  }
}
```

Grok 和 Codex 的项目配置里这段保持 `enabled = false`。要用时改成 `true`，用完改回 `false`。Claude 只在这次任务把 `wechat-mp-download` 加进 `enabledMcpjsonServers`。Cursor 从 `.cursor/mcp.on-demand.json` 复制到 `.cursor/mcp.json`，用完移回。不要写进用户级 `~/.grok`、`~/.codex`、`~/.claude.json` 或 `~/.cursor`。

独立运行时，把 `PYTHONPATH` 换成仓库根目录，或改用 `cwd` 指向仓库根目录。数据目录建议单独指定，避免和别的任务共用会话：

```text
python -m wechatdownload.server --data D:\data\wechat-mp
```

旧的本机 HTTP 入口只监听环回地址，不作为项目目录的正式接入：

```text
python -m wechatdownload.server --transport streamable-http --port 4545
```

地址是 `http://127.0.0.1:4545/mcp`。

## 会话

历史和合集任务需要微信在打开公众号页时写出的 `uin`、`key`、`pass_ticket`。每个公众号、每次密钥失效，都做一次：

1. `prepare_account`，传入文章链接、合集链接，或含 `__biz` 的文本。
2. 在已经登录的微信电脑版里打开返回的 `confirmation_url`，等公众号页面加载完。
3. `capture_session`。它扫描 `%USERPROFILE%\AppData\Roaming\Tencent\xwechat` 和 `WeChat`。
4. 目录里扫不到时，把微信里复制出的、带 `uin` 和 `key` 的 `mp.weixin.qq.com` 链接交给 `import_session_url`。
5. `session_status` 的 `ready` 为 true 之后，再列历史或下合集。

`session_status` 只返回这些公开字段：`ready`、`biz`、`uin_hint`、`has_pass_ticket`、`has_poc_token`、`verified`、`verified_at`、`age_seconds`、`source`。

密钥失效时，历史任务变成 `needs_session`，并记下 `resume_offset`。重新完成第 2 步和第 3 步后，`resume_job` 从该偏移继续。`cancelled` 和 `failed` 也可以恢复。`done` 不能恢复。

## 工具

| 工具 | 作用 | 主要参数 |
| --- | --- | --- |
| `prepare_account` | 提取 `__biz`，返回确认链接 | `text` |
| `capture_session` | 扫描本机微信目录并逐条校验 | `root`，空则用默认目录 |
| `import_session_url` | 从复制出的链接导入并校验 | `url` |
| `session_status` | 会话是否有效 | 无 |
| `list_history` | 同步列出历史，最多 30 页 | `max_pages` 默认 3 |
| `download_history` | 后台下载历史 | `max_pages` 为 0 时翻到结束；`delay_seconds` 默认 1；`save_pages` 默认 true |
| `download_one` | 下载单篇 HTML | `url` |
| `list_album` | 列出合集或主页，默认最多 5 页 | `url`、`max_pages` |
| `download_album` | 后台下载合集或主页 | `url`、`max_pages` 为 0 时翻完、`save_markdown` |
| `job_status` | 查看任务。`job_id` 空则看最近一个 | `job_id` |
| `job_cancel` | 取消后台任务 | `job_id` |
| `resume_job` | 从上次偏移继续 | `job_id` |
| `export_manifest` | 返回清单路径 | `job_id`、`export_format` 为 `csv` 或 `json` |
| `diagnose_page` | 对照切片解析和修正解析 | `path` 必须在数据目录内，或直接给 `body`。正文上限 2_000_000 字符 |

历史和合集共用的过滤参数：

| 参数 | 含义 |
| --- | --- |
| `original_only` | 只保留 `copyright_stat` 或 `copyright_type` 为 1 的文章 |
| `date_mode` | `all`、`skip`、`stop`。历史从新到旧 |
| `start_date` / `end_date` | `YYYY-MM-DD` |
| `min_reads` | 阅读量低于此值则跳过。0 表示不限制 |
| `start_page` | 从第几页开始，第一页偏移是 0 |
| `start_offset` | `download_history` 专用。小于 0 表示不指定 |

`date_mode=skip` 时，窗口外的文章跳过并继续翻页。`date_mode=stop` 时，新于结束日的跳过；旧于开始日的文章让整个任务停下，因为再往前只会更旧。没有发布时间的文章保留。

`download_history` 的 `start_offset` 传 `-1` 表示沿用 `start_page`。

## 案例

下面的链接、公众号和合集是本仓库接入后实际请求过的公开页面。账号是「ai辅助逆向手记」，`__biz` 是 `MzkxMzMxMjM0Ng==`。

### 1. 下载一篇公开文章

不需要会话。

```text
download_one
  url: https://mp.weixin.qq.com/s/q3P0nQlIRnybYMyvlVrNFQ
```

短链里没有 `__biz`。工具会请求正文，从页面取出标题。这次返回：

```json
{
  "ok": true,
  "title": "JSVMP 进阶：VM套娃",
  "kind": "html",
  "saved_path": "<数据目录>/articles/MzkxMzMxMjM0Ng==/unknown-time_JSVMP 进阶VM套娃.html"
}
```

`session_status` 仍然是 `ready: false`。页面若是「请在微信客户端打开链接」，且没有 `js_content` 或 `cdn_url`，返回 `ok: false`，并带上要在微信里打开确认页的说明。

等价的 Python：

```python
from pathlib import Path
from wechatdownload.app import App

app = App(Path("data"))
print(app.download_one("https://mp.weixin.qq.com/s/q3P0nQlIRnybYMyvlVrNFQ"))
```

### 2. 一个作者的公开合集

同一篇文章属于合集「jsvmp从入门到入土」，`album_id` 为 `4685428779056660482`，服务端 `article_count` 为 12。合集地址：

```text
https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzkxMzMxMjM0Ng==&action=getalbum&album_id=4685428779056660482
```

已校验会话之后，用 MCP 一次列完或下完：

```text
list_album
  url: <上面的合集地址>
  max_pages: 5

download_album
  url: <上面的合集地址>
  max_pages: 0
```

没有会话时，`list_album` 和 `download_album` 直接返回「会话无效」，不会去请求合集。这和单篇不同。账号全量历史同样没有会话：直接请求 `getmsg` 得到 `ret=-3`、`errmsg=no session`。本机 `xwechat` 目录当时也没有扫到可用密钥。

这 12 篇的正文可以用案例 1 的 `download_one` 逐篇保存，不依赖会话。合集顺序是：

1. JSVMP 从入门到入土（序）
2. JSVMP 从入门到入土（一）
3. JSVMP 从入门到入土（二）
4. JSVMP 从入门到入土（三）
5. JSVMP 从入门到入土（四）
6. JSVMP 从入门到入土（终）
7. JSVMP 进阶：分派器长什么样，怎么各个击破
8. JSVMP 进阶：反调试墙
9. JSVMP 进阶：断点下满了却一次不命中，VM从哪里来
10. JSVMP 进阶：真真假假，假假真真
11. JSVMP进阶：自修改字节码
12. JSVMP 进阶：VM套娃

合集之外的文章不在这 12 篇里。要下整个公众号，走案例 3。

### 3. 下载一个公众号的历史

```text
prepare_account
  text: https://mp.weixin.qq.com/s/q3P0nQlIRnybYMyvlVrNFQ
```

返回里有 `biz` 和 `confirmation_url`。这个账号的确认页是：

```text
https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=MzkxMzMxMjM0Ng==&scene=124#wechat_redirect
```

在微信电脑版打开并等页面加载完，然后：

```text
capture_session
session_status
```

`ready` 为 true 后先看前三页，再后台翻完：

```text
list_history
  max_pages: 3

download_history
  max_pages: 0
  delay_seconds: 1
  original_only: false
  date_mode: all
  min_reads: 0
```

`download_history` 立即返回，例如：

```json
{
  "ok": true,
  "job_id": "0123456789ab",
  "kind": "history",
  "status": "queued",
  "biz": "MzkxMzMxMjM0Ng=="
}
```

接着：

```text
job_status
  job_id: 0123456789ab
```

`status` 变成 `done` 后：

```text
export_manifest
  job_id: 0123456789ab
  export_format: json
```

`summary` 含 `listed`、`downloaded`、`skipped`、`stopped`、`titles`。`titles` 最多 20 条。完整结果在清单里。

只下 2024 年的原创，并且阅读量至少 100：

```text
download_history
  max_pages: 0
  original_only: true
  date_mode: stop
  start_date: 2024-01-01
  end_date: 2024-12-31
  min_reads: 100
```

`stop` 适合历史从新到旧：遇到早于 `start_date` 的文章就停，避免把更早的页面也请求一遍。只要跳过窗口外的文章、后面还继续，用 `date_mode: skip`。

### 4. 密钥失效后继续

历史任务若遇到「请在微信客户端打开链接」，`job_status` 类似：

```json
{
  "ok": true,
  "status": "needs_session",
  "resume_offset": 20,
  "error": "请在微信客户端打开链接"
}
```

重新打开确认页并 `capture_session` 成功后：

```text
resume_job
  job_id: 0123456789ab
```

它从 `resume_offset` 继续，不从头再下已经记下的正文。`job_cancel` 之后也可以对同一 `job_id` 调用 `resume_job`。

### 5. 对照漏文

`download_history` 默认把每一页 `getmsg` 原文写到 `jobs/<job_id>/_pages/offset-<n>.json`。对其中一页做对照：

```text
diagnose_page
  path: <数据目录>/jobs/<job_id>/_pages/offset-0.json
```

返回 `legacy_count`、`fixed_count`、`only_fixed_count` 和最多 20 条 `only_fixed`。`only_fixed` 是修正解析多出来的文章，也就是 4.6 切片会漏掉的那部分。`path` 必须位于当前数据目录，不能读目录外的文件。

命令行对照一份已经保存的响应：

```text
python -m wechatdownload diagnose jobs\某任务\_pages\offset-0.json
```

切片会漏文的几种页面：`next_offset` 出现在列表前面，列表和 `next_offset` 之间还有别的字段，`general_msg_list` 已经是对象而不是字符串，或者 JSON 里有空格。修正解析接受字符串或对象，并读取 `next_offset` 和 `can_msg_continue`。旧逻辑每次把偏移加 10，服务端给出的下一偏移若有空隙，中间那一窗不会再请求。

一篇推送里的 `multi_app_msg_item_list` 和 `app_msg_ext_info_list` 都会展开。空的 `content_url` 记入清单，原因是 `empty_content_url`，而不是丢掉。

## 命令行

命令行和 MCP 做同一类事，但密钥从参数或 `keys.local.json` 读取，不从 `session.json` 读取。`keys.local.json` 含密钥，已在 `.gitignore` 里。终端不打印密钥。

```text
python -m wechatdownload biz "https://mp.weixin.qq.com/s/q3P0nQlIRnybYMyvlVrNFQ"
python -m wechatdownload keys --out keys.local.json
python -m wechatdownload verify --credential keys.local.json --biz MzkxMzMxMjM0Ng==
python -m wechatdownload list --credential keys.local.json --biz MzkxMzMxMjM0Ng== --max-pages 3 --out data\list
python -m wechatdownload download --credential keys.local.json --biz MzkxMzMxMjM0Ng== --max-pages 1 --delay 1 --save-pages --out data\history
python -m wechatdownload album --credential keys.local.json --biz MzkxMzMxMjM0Ng== --url "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=MzkxMzMxMjM0Ng==&album_id=4685428779056660482" --out data\album.json
python -m wechatdownload diagnose data\history\_pages\offset-0.json
```

`list` 和 `download` 还接受 `--parser fixed|legacy`、`--offset server|legacy`、`--original-only`、`--date-mode`、`--start-date`、`--end-date`、`--min-reads`、`--strict-reads`、`--stats`、`--markdown`、`--proxy`。`--stats` 会请求阅读量，但不因为阅读量跳过；和 `--min-reads` 一起用时才按阅读量过滤。`--strict-reads` 在阅读量接口失败时也跳过。

`biz`、`uin`、`key` 也可以直接作为参数传入。不要把它们写进提交说明、日志或仓库。

## 清单和任务状态

清单编码是 UTF-8 BOM，列是：

`kind`、`action`、`reason`、`title`、`url`、`source`、`published_at`、`offset`、`message`、`saved_path`

`action` 为 `list`、`download`、`skip` 或 `stop`。常见 `reason`：

| reason | 含义 |
| --- | --- |
| `empty_content_url` | 这条没有正文链接，已记录 |
| `not_original` | 开了只下原创 |
| `outside_date` / `after_end_date` | 不在日期窗口内 |
| `before_start_date` | `stop` 模式下已经早于开始日，任务停下 |
| `low_reads` | 阅读量低于下限 |
| `read_count_unavailable` | 阅读量接口失败，且开了严格模式 |
| `fetch_failed` | 这一篇正文没下下来，任务继续 |
| `key_expired` | 整页密钥失效，任务变为 `needs_session` |
| `api_ret` | 接口 `ret` 不是成功 |
| `legacy_parse` / `parse` | 这一页解析失败。`legacy` 解析失败会停掉后面的页 |
| `empty_page` | 这一页没有消息 |
| `cancelled` | 任务被取消 |

任务状态是 `queued`、`running`、`done`、`needs_session`、`cancelled`、`failed`。

## 开发

```text
python -m unittest discover -s tests -v
```

在 reverse_ENV 里从本目录执行：

```text
D:\reverse_ENV\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试不访问微信，传输和目录扫描都是注入的。当前 19 项。

设计边界见 `docs/architecture.md`。

## 仓库范围

这是独立的 Private 仓库。解包出的 exe、dll、pyd、常量转储、会话、密钥和下载结果不进 Git。

reverse_ENV 只用 gitlink 固定 `mcp/wechat-mp-download`，不设置浮动 submodule 分支。更新代码时先在本仓库提交并推送，再在主仓移动 gitlink。
