# 架构

使用方式、参数和实测案例见仓库根目录的 `README.md`。本文只记录边界。

```text
MCP 客户端或命令行
  │ stdio，或仅绑定 127.0.0.1 的 streamable HTTP
  ▼
wechatdownload.server / wechatdownload.cli
  ▼
wechatdownload.app
  ├─ store.SessionStore     session.json，对外只有 public()
  ├─ Job                    一个后台线程，清单写在数据目录
  └─ client / listing / album / article / session
        ▼
   mp.weixin.qq.com
```

## 会话

`uin`、`key`、`pass_ticket` 由已登录的微信电脑版在打开公众号页时写出。程序不计算这些值。

`prepare_account` 给出确认链接。用户把它发给微信电脑版的「文件传输助手」并点开，等作者页加载完后，`capture_session` 扫描本机 `xwechat` 或 `WeChat` 目录，包括 cache 和带 `uin=` 的二进制日志。作者页没有复制链接的按钮，不向用户要页面 URL。任务结束时把 `options` 和 `done_urls` 写入 `job.json`，下次启动可以 `resume_job`。`import_session_url` 只接收调用方已经持有的、带 `uin` 和 `key` 的链接。校验请求是 `profile_ext?action=home`。正文里出现「请在微信客户端打开链接」视为密钥失效。

`download_one` 不要求已校验会话。它直接请求文章 URL。保存目录优先用会话里的 `biz`，否则用页面里的 `biz`。页面要求在微信内打开、且没有 `js_content` 或 `cdn_url` 时返回失败，不写入文件。

`list_history` 和 `download_history` 在发请求前调用 `SessionStore.require()`。没有已校验会话时停在工具层，不会发出空的 `getmsg`。空的 `getmsg` 在接口上的表现是 `ret=-3`、`errmsg=no session`。

`list_album` 和 `download_album` 在已校验会话不存在时直接返回确认链接，不请求合集，也不下载文章页上的可见文章。会话就绪后才带上 `uin` 和 `key`。历史恢复和合集恢复都要会话。

默认工具面是 `agent`：`mp_session`、`mp_fetch`、`mp_history`、`mp_job`、`mp_diagnose`。`WECHAT_MP_TOOLSET=full` 才注册 14 个工具。服务启动不扫描微信目录，也不访问网络。`mp_fetch` 的摘录从 `js_content` 起截取有限片段再去标签，不把整页交给 Markdown 转换。工具结果不返回 HTML。历史任务默认不把 getmsg 原文落盘。

## 任务

长任务立即返回 `job_id`。同一时刻只跑一个任务。状态是 `queued`、`running`、`done`、`needs_session`、`cancelled`、`failed`。

历史任务遇到密钥失效时记下 `resume_offset`。`resume_job` 只接受 `needs_session`、`cancelled`、`failed`，并从该偏移继续。合集恢复时跳过 `done_urls` 里已经下载的链接。

## 列表

历史默认用修正解析：`general_msg_list` 可以是字符串或对象，读取 `next_offset` 和 `can_msg_continue`。4.6 的切片解析和每次偏移加 10 只在 `parser=legacy`、`offset=legacy` 或 `diagnose_page` 中使用。

一篇推送展开主条、`multi_app_msg_item_list` 和 `app_msg_ext_info_list`。空链接记为 `empty_content_url`。

合集和主页走 `action=getalbum`，不走 `getmsg`。主页 HTML 先抽出合集链接，再逐个请求。公开合集在空 `uin`、空 `key` 下可以列出。

## 保存

主机名必须是 `mp.weixin.qq.com`。单篇写到 `articles/<biz>/`。历史和合集任务写到 `jobs/<job_id>/`，清单是 UTF-8 BOM 的 CSV。`diagnose_page` 的 `path` 必须位于数据目录内。

`save_markdown` 只转 `#js_content`（没有该节点时转 body）。输出保留标题层级、段落、列表、表格、引用、删除线、代码块、公式、`data-src` 图片、背景图，以及语音、视频、音乐卡片。去掉页面壳、代码块行号和 `counter(line)` 泄漏。不依赖 `html2text`。参考了 jackwener/wechat-article-to-markdown 的代码块预处理，以及 wechat-article/wechat-article-exporter 的懒加载图片和页面噪声节点。历史展开会递归副条，链接字段接受 `content_url`、`url`、`link`、`source_url`。合集 `getalbum_resp` 可以是对象或字符串，`article_list` 可以是数组或单条对象；倒序翻页看 `reverse_continue_flag`。历史 `item_show_type` 只命名（0 图文、5 视频、6 音乐、7 音频、8 图片、10 文本、17 短内容），默认不过滤。代码块按 `.code-snippet__fix` 整段抽取。

工具结果、清单和任务 JSON 不包含密钥原文。
