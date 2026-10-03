from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

LIST_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)
ARTICLE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 NetType/WIFI "
    "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090a1b) XWEB/8555 Flue"
)


class HttpError(RuntimeError):
    pass


class UrllibTransport:
    def __init__(self, cookie: str = "", proxy: str = "", timeout: float = 30):
        handlers = []
        if proxy:
            handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
        self.opener = build_opener(*handlers)
        self.cookie = cookie
        self.timeout = timeout

    def get_text(self, url: str, user_agent: str = LIST_UA) -> str:
        return self._read(url, method="GET", data=None, user_agent=user_agent)

    def post_form(self, url: str, form: dict, user_agent: str = ARTICLE_UA) -> str:
        data = urlencode({key: "" if value is None else str(value) for key, value in form.items()}).encode("utf-8")
        return self._read(url, method="POST", data=data, user_agent=user_agent)

    def _read(self, url: str, method: str, data: bytes | None, user_agent: str) -> str:
        headers = {
            "User-Agent": user_agent,
            "Referer": "https://mp.weixin.qq.com/",
            "Cache-Control": "no-cache",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise HttpError(f"HTTP {exc.code}: {detail[:300]}") from exc
        except URLError as exc:
            raise HttpError(str(exc.reason)) from exc
        return raw.decode("utf-8", errors="replace")


def loads_json(text: str) -> dict:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise HttpError("JSON 顶层不是对象")
    return data
