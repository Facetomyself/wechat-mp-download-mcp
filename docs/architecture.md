# 架构

服务跑在已经登录微信电脑版的这台机器上。MCP 是无界面入口，下载库不依赖窗口。

```text
MCP 客户端
  │ stdio，或仅绑定 127.0.0.1 的 streamable HTTP
  ▼
wechatdownload.server
  ▼
wechatdownload.app
  ├─ store.SessionStore     会话文件，工具结果不含密钥
  ├─ Job                    后台线程，清单写在数据目录
  └─ client / listing / album / article / session
        ▼
   mp.weixin.qq.com
```

`uin`、`key`、`pass_ticket` 由微信在打开公众号页时写出。`prepare_account` 给出确认链接，用户在微信里打开，然后 `capture_session` 扫描本机目录，或 `import_session_url` 接收复制出的链接。

长任务立即返回 `job_id`。密钥失效时状态是 `needs_session`，记下偏移；重新捕获后 `resume_job` 从该偏移继续。同一时刻只跑一个任务。

请求主机固定为 `mp.weixin.qq.com`。历史列表默认用修正后的 JSON 解析，不再用 4.6 的 `general_msg_list` 切片。
