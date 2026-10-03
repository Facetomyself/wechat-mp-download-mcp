from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wechatdownload.app import App, ensure_mp_url
from wechatdownload.models import Credential
from wechatdownload.server import AGENT_NAMES, FULL_NAMES, build_server
from wechatdownload.urls import KEY_EXPIRED_TEXT


def compact(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def history_page(title: str, url: str, next_offset: int, can_continue: int) -> str:
    message = {
        "comm_msg_info": {"datetime": 1700000000, "type": 49, "id": 1},
        "app_msg_ext_info": {
            "title": title,
            "content_url": url,
            "copyright_stat": 1,
            "copyright_type": 1,
        },
    }
    return compact(
        {
            "ret": 0,
            "errmsg": "ok",
            "can_msg_continue": can_continue,
            "general_msg_list": compact({"list": [message]}),
            "next_offset": next_offset,
        }
    )


class Scripted:
    def __init__(self) -> None:
        self.pages: dict[int, str] = {}
        self.articles: dict[str, str] = {}
        self.album: dict[str, str] = {}

    def get_text(self, url: str, user_agent: str | None = None) -> str:
        if "action=home" in url:
            if "key=bad" in url:
                return KEY_EXPIRED_TEXT
            return "公众号首页"
        if "action=getmsg" in url:
            offset = int(parse_qs(urlparse(url).query)["offset"][0])
            return self.pages[offset]
        if url in self.articles:
            return self.articles[url]
        for needle, body in self.album.items():
            if needle in url:
                return body
        raise AssertionError(url)


ARTICLE = "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=5&idx=1&sn=ggg"
HTML = '<html><head><meta property="og:title" content="甲"></head><body><div id="js_content"><p>hi</p></div></body></html>'
SECRET = "secretkey"


class McpAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.transport = Scripted()
        self.app = App(self.root, transport=self.transport, scan=self._scan)
        self.seen_keys: list[str] = []

    def tearDown(self) -> None:
        for thread in list(self.app._threads.values()):
            thread.join(timeout=5)
        self.tmp.cleanup()

    def _scan(self, roots: list[Path] | None = None) -> list[Credential]:
        return [Credential(uin="11112222", key=SECRET, pass_ticket="ticket", source="mem")]

    def _import(self, key: str = SECRET) -> dict:
        prepared = self.app.prepare_account("https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1&idx=1")
        self.assertTrue(prepared["ok"])
        self.assertIn("__biz=Abcd1234", prepared["confirmation_url"])
        return self.app.import_session_url(
            f"https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=Abcd1234&uin=11112222&key={key}&pass_ticket=ticket&scene=124"
        )

    def test_prepare_rejects_other_hosts_and_hides_secrets(self) -> None:
        rejected = self.app.prepare_account("https://example.com/s?__biz=Abcd1234")
        self.assertFalse(rejected["ok"])
        with self.assertRaises(ValueError):
            ensure_mp_url("https://example.com/a")
        imported = self._import()
        self.assertTrue(imported["ready"])
        self.assertNotIn(SECRET, json.dumps(imported, ensure_ascii=False))
        self.assertEqual(imported["uin_hint"], "…2222")
        captured = self.app.capture_session()
        self.assertTrue(captured["ready"])
        self.assertNotIn(SECRET, json.dumps(captured, ensure_ascii=False))

    def test_expired_import_does_not_become_ready(self) -> None:
        self.app.prepare_account("https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1")
        result = self.app.import_session_url(
            "https://mp.weixin.qq.com/mp/profile_ext?action=home&__biz=Abcd1234&uin=11112222&key=bad&pass_ticket=ticket&x=1"
        )
        self.assertFalse(result["ok"])
        self.assertFalse(self.app.session_status()["ready"])

    def test_history_job_resumes_after_key_expiry(self) -> None:
        self._import()
        self.transport.pages[0] = KEY_EXPIRED_TEXT
        started = self.app.download_history(max_pages=2, delay_seconds=0, save_pages=False)
        self.assertTrue(started["ok"])
        self.app.wait_job(started["job_id"])
        status = self.app.job_status(started["job_id"])
        self.assertEqual(status["status"], "needs_session")
        self.assertEqual(status["resume_offset"], 0)
        self.transport.pages[0] = history_page("甲", ARTICLE, 10, 0)
        self.transport.articles[ARTICLE] = HTML
        resumed = self.app.resume_job(started["job_id"])
        self.assertIn(resumed["status"], {"queued", "running", "done"})
        self.app.wait_job(started["job_id"])
        done = self.app.job_status(started["job_id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["summary"]["downloaded"], 1)
        self.assertNotIn(SECRET, Path(done["manifest_path"]).read_text(encoding="utf-8"))
        exported = self.app.export_manifest(started["job_id"], "json")
        self.assertTrue(exported["ok"])
        self.assertTrue(Path(exported["path"]).is_file())

    def test_list_download_one_and_diagnose(self) -> None:
        self._import()
        self.transport.pages[0] = history_page("甲", ARTICLE, 10, 0)
        listed = self.app.list_history(max_pages=1)
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["titles"], ["甲"])
        self.transport.articles[ARTICLE] = HTML
        saved = self.app.download_one(ARTICLE)
        self.assertTrue(saved["ok"])
        self.assertTrue(Path(saved["saved_path"]).is_file())

    def test_download_one_public_page_does_not_need_session(self) -> None:
        self.transport.articles[ARTICLE] = HTML
        saved = self.app.download_one(ARTICLE)
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["title"], "甲")
        self.assertFalse(self.app.session_status()["ready"])
        self.assertNotIn(SECRET, json.dumps(saved, ensure_ascii=False))

    def test_download_one_wechat_gate_stays_closed(self) -> None:
        self.transport.articles[ARTICLE] = f"<html><body>{KEY_EXPIRED_TEXT}</body></html>"
        saved = self.app.download_one(ARTICLE)
        self.assertFalse(saved["ok"])
        self.assertEqual(saved["error"], KEY_EXPIRED_TEXT)
        self.assertFalse(list((self.root / "articles").glob("*")))
        body = compact(
            {
                "ret": 0,
                "can_msg_continue": 1,
                "general_msg_list": {
                    "list": [
                        {
                            "comm_msg_info": {"datetime": 1700000000},
                            "app_msg_ext_info": {"title": "对象", "content_url": ARTICLE, "copyright_stat": 1},
                        }
                    ]
                },
                "next_offset": 10,
            }
        )
        diff = self.app.diagnose_page(body=body)
        self.assertTrue(diff["ok"])
        self.assertGreaterEqual(diff["only_fixed_count"], 1)
        outside = self.app.diagnose_page(path=str(Path(self.tmp.name).parent / "outside.json"))
        self.assertFalse(outside["ok"])

    def test_album_list_and_registered_tools(self) -> None:
        self._import()
        link = "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Abcd1234&album_id=99"
        self.transport.album["homepage"] = f'<html><a href="{link}">合集</a></html>'
        self.transport.album["action=getalbum"] = compact(
            {
                "getalbum_resp": {
                    "continue_flag": 0,
                    "article_list": [{"title": "合集文", "url": ARTICLE, "msgid": "9", "itemidx": "1"}],
                }
            }
        )
        listed = self.app.list_album("https://mp.weixin.qq.com/mp/homepage?__biz=Abcd1234")
        self.assertEqual(listed["titles"], ["合集文"])
        names = {tool.name for tool in build_server(self.app, toolset="full")._tool_manager.list_tools()}
        self.assertEqual(names, set(FULL_NAMES))

    def test_public_album_agent_toolset_and_bounded_excerpt(self) -> None:
        link = "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Abcd1234&album_id=99"
        self.transport.album["action=getalbum"] = compact(
            {
                "getalbum_resp": {
                    "continue_flag": 0,
                    "article_list": [{"title": "合集文", "url": ARTICLE, "msgid": "9", "itemidx": "1"}],
                }
            }
        )
        listed = self.app.list_album(link)
        self.assertTrue(listed["ok"])
        self.assertEqual(listed["titles"], ["合集文"])
        self.assertFalse(self.app.session_status()["ready"])
        self.transport.articles[ARTICLE] = HTML
        started = self.app.download_album(link)
        self.assertTrue(started["ok"])
        self.app.wait_job(started["job_id"])
        done = self.app.job_status(started["job_id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["summary"]["downloaded"], 1)

        info = self.app.fetch(ARTICLE, mode="info")
        self.assertTrue(info["ok"])
        self.assertEqual(info["excerpt"], "hi")
        self.assertNotIn("<", info["excerpt"])
        self.assertNotIn("saved_path", info)
        article_dir = self.root / "articles"
        before = list(article_dir.rglob("*")) if article_dir.exists() else []
        huge = (
            "<html><head><meta property=\"og:title\" content=\"甲\"></head><body>"
            "<script>SECRETTOKEN uin=111 key=abc</script>"
            "<div id=\"js_content\"><p>" + ("字" * 5000) + "</p></div></body></html>"
        )
        self.transport.articles[ARTICLE] = huge
        peeked = self.app.fetch(ARTICLE, mode="info", excerpt_chars=80)
        self.assertLessEqual(len(peeked["excerpt"]), 80)
        self.assertNotIn("SECRETTOKEN", peeked["excerpt"])
        self.assertNotIn("js_content", peeked["excerpt"])
        self.assertNotIn("<", peeked["excerpt"])
        self.assertEqual(list(article_dir.rglob("*")) if article_dir.exists() else [], before)
        saved = self.app.fetch(ARTICLE, mode="save", excerpt_chars=80)
        self.assertTrue(saved["ok"])
        self.assertLessEqual(len(saved["excerpt"]), 80)
        self.assertTrue(Path(saved["saved_path"]).is_file())

        agent_tools = list(build_server(self.app, toolset="agent")._tool_manager.list_tools())
        self.assertEqual({tool.name for tool in agent_tools}, set(AGENT_NAMES))
        described = " ".join(tool.description or "" for tool in agent_tools)
        self.assertLess(len(described), 500)
        diagnose = next(tool for tool in agent_tools if tool.name == "mp_diagnose")
        rejected = diagnose.fn(body="x" * 20_001)
        self.assertFalse(rejected["ok"])


if __name__ == "__main__":
    unittest.main()
