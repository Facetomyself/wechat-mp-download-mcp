from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wechatdownload.album import crawl_album, parse_album_response
from wechatdownload.article import parse_like_num, select_article_html, write_article_files
from wechatdownload.biz import extract_biz, extract_biz_legacy
from wechatdownload.client import CrawlOptions, HistoryCrawler
from wechatdownload.filters import decide_article
from wechatdownload.listing import diff_parsers, flatten_messages, parse_getmsg, parse_getmsg_legacy, show_type_name
from wechatdownload.listing import ParseError
from wechatdownload.models import ArticleRef
from wechatdownload.session import credentials_in_text, scan_credentials
from wechatdownload.urls import normalize_content_url


def compact(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def message(title: str, url: str, copyright_stat: int = 1, multi: list | None = None, ts: int = 1700000000) -> dict:
    ext = {
        "title": title,
        "content_url": url,
        "copyright_stat": copyright_stat,
        "copyright_type": copyright_stat,
    }
    if multi:
        ext["multi_app_msg_item_list"] = multi
    return {"comm_msg_info": {"datetime": ts, "type": 49, "id": 1}, "app_msg_ext_info": ext}


def string_page(messages: list[dict], next_offset: int, can_continue: int = 1) -> str:
    return compact(
        {
            "ret": 0,
            "errmsg": "ok",
            "can_msg_continue": can_continue,
            "general_msg_list": compact({"list": messages}),
            "next_offset": next_offset,
        }
    )


class MapTransport:
    def __init__(self, pages: dict[int, str], articles: dict[str, str] | None = None, stats: dict | None = None):
        self.pages = pages
        self.articles = articles or {}
        self.stats = stats or {"appmsgstat": {"read_num": 100, "like_num": 1, "old_like_num": 4, "share_num": 2}}
        self.offsets: list[int] = []

    def get_text(self, url: str, user_agent: str | None = None) -> str:
        if "action=getmsg" in url:
            offset = int(parse_qs(urlparse(url).query)["offset"][0])
            self.offsets.append(offset)
            if offset not in self.pages:
                raise AssertionError(f"unexpected offset {offset}")
            return self.pages[offset]
        if url in self.articles:
            return self.articles[url]
        raise AssertionError(url)

    def post_form(self, url: str, form: dict, user_agent: str | None = None) -> str:
        return compact(self.stats)


class ListingTests(unittest.TestCase):
    def test_legacy_and_fixed_agree_on_compact_string_page(self) -> None:
        raw_url = "https://mp.weixin.qq.com/s?__biz=Abcd1234&amp;mid=100&amp;idx=1&amp;sn=aaa&amp;foo=1"
        multi = [{"title": "副条", "content_url": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=100&idx=2&sn=ccc", "copyright_stat": 0, "copyright_type": 0}]
        body = string_page([message("主条", raw_url, multi=multi)], 10)
        diff = diff_parsers(body)
        self.assertIsNone(diff.legacy_error)
        self.assertEqual(diff.only_fixed, [])
        self.assertEqual(diff.next_offset, 10)
        self.assertTrue(diff.can_continue)
        articles = flatten_messages(parse_getmsg(body).items)
        self.assertEqual([item.title for item in articles], ["主条", "副条"])
        nested = {
            "title": "外层",
            "link": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=2&idx=1&sn=ooo",
            "multi_app_msg_item_list": [
                {"title": "内层", "content_url": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=3&idx=1&sn=iii"}
            ],
        }
        nested_titles = [item.title for item in flatten_messages([message("主条", raw_url, multi=[nested])])]
        self.assertEqual(nested_titles, ["主条", "外层", "内层"])
        self.assertEqual(
            articles[0].url,
            "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=100&idx=1&sn=aaa",
        )
        self.assertNotIn("foo=", articles[0].url)

    def test_slice_misses_when_next_offset_is_before_list(self) -> None:
        body = compact(
            {
                "ret": 0,
                "next_offset": 10,
                "can_msg_continue": 1,
                "general_msg_list": compact({"list": [message("晚到的字段", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1&idx=1&sn=aaa")]}),
            }
        )
        with self.assertRaises(ParseError):
            parse_getmsg_legacy(body)
        self.assertEqual(parse_getmsg(body).items[0]["app_msg_ext_info"]["title"], "晚到的字段")
        diff = diff_parsers(body)
        self.assertEqual(len(diff.only_fixed), 1)

    def test_slice_misses_when_another_field_sits_between(self) -> None:
        inner = compact({"list": [message("被截断", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=2&idx=1&sn=bbb")]})
        quoted = json.dumps(inner, ensure_ascii=False, separators=(",", ":"))
        body = '{"ret":0,"general_msg_list":' + quoted + ',"video_count":0,"next_offset":10}'
        with self.assertRaises(ParseError):
            parse_getmsg_legacy(body)
        self.assertEqual(len(parse_getmsg(body).items), 1)

    def test_slice_misses_object_list_and_spaced_json(self) -> None:
        payload = {
            "ret": 0,
            "can_msg_continue": 1,
            "general_msg_list": {"list": [message("对象", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=3&idx=1&sn=ccc")]},
            "next_offset": 10,
        }
        body = compact(payload)
        with self.assertRaises(ParseError):
            parse_getmsg_legacy(body)
        self.assertEqual(parse_getmsg(body).next_offset, 10)
        spaced = json.dumps(
            {
                "ret": 0,
                "general_msg_list": compact({"list": [message("空格", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=4&idx=1&sn=ddd")]}),
                "next_offset": 10,
            }
        )
        with self.assertRaises(ParseError):
            parse_getmsg_legacy(spaced)
        self.assertEqual(len(parse_getmsg(spaced).items), 1)

    def test_empty_content_url_is_kept(self) -> None:
        articles = flatten_messages([message("没有链接", "")])
        self.assertEqual(articles[0].url, "")
        outcome = decide_article(articles[0])
        self.assertEqual(outcome.reason, "empty_content_url")


class EngineTests(unittest.TestCase):
    def test_legacy_offset_stops_before_server_gap(self) -> None:
        pages = {
            0: string_page([message("A", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1&idx=1&sn=aaa")], 20, 1),
            10: string_page([], 10, 0),
            20: string_page([message("C", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=3&idx=1&sn=ccc")], 20, 0),
        }
        legacy = HistoryCrawler("Abcd1234", "1", "k", "t", MapTransport(pages), CrawlOptions(parser="legacy", offset_mode="legacy", max_pages=5))
        legacy_titles = [row.title for row in legacy.run() if row.action == "list"]
        fixed_transport = MapTransport(pages)
        fixed = HistoryCrawler("Abcd1234", "1", "k", "t", fixed_transport, CrawlOptions(parser="fixed", offset_mode="server", max_pages=5))
        fixed_titles = [row.title for row in fixed.run() if row.action == "list"]
        self.assertEqual(legacy_titles, ["A"])
        self.assertEqual(fixed_titles, ["A", "C"])
        self.assertEqual(fixed_transport.offsets, [0, 20])

    def test_legacy_parser_stops_the_rest_of_history(self) -> None:
        pages = {
            0: compact(
                {
                    "ret": 0,
                    "can_msg_continue": 1,
                    "general_msg_list": {"list": [message("A", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1&idx=1&sn=aaa")]},
                    "next_offset": 10,
                }
            ),
            10: string_page([message("B", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=2&idx=1&sn=bbb")], 10, 0),
        }
        legacy_rows = HistoryCrawler("Abcd1234", "1", "k", "t", MapTransport(pages), CrawlOptions(parser="legacy", offset_mode="server")).run()
        self.assertTrue(any(row.reason == "legacy_parse" for row in legacy_rows))
        self.assertNotIn("B", [row.title for row in legacy_rows])
        fixed_rows = HistoryCrawler("Abcd1234", "1", "k", "t", MapTransport(pages), CrawlOptions()).run()
        self.assertEqual([row.title for row in fixed_rows if row.action == "list"], ["A", "B"])

    def test_original_date_and_read_filters(self) -> None:
        multi = [{"title": "转载", "content_url": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=8&idx=2&sn=eee", "copyright_stat": 0, "copyright_type": 0}]
        newer = int(datetime(2024, 6, 2, 12, 0).timestamp())
        older = int(datetime(2024, 4, 1, 12, 0).timestamp())
        pages = {
            0: string_page(
                [
                    message("原创", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=8&idx=1&sn=ddd", multi=multi, ts=newer),
                    message("太旧", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=7&idx=1&sn=fff", ts=older),
                ],
                10,
                1,
            )
        }
        options = CrawlOptions(
            original_only=True,
            date_mode="stop",
            start_date=date(2024, 5, 1),
            end_date=date(2024, 6, 1),
            max_pages=3,
        )
        rows = HistoryCrawler("Abcd1234", "1", "k", "t", MapTransport(pages), options).run()
        actions = {(row.title, row.action, row.reason) for row in rows}
        self.assertIn(("原创", "skip", "after_end_date"), actions)
        self.assertIn(("转载", "skip", "not_original"), actions)
        self.assertIn(("太旧", "stop", "before_start_date"), actions)

        read_pages = {0: string_page([message("热文", "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=5&idx=1&sn=ggg")], 10, 0)}
        low = HistoryCrawler(
            "Abcd1234",
            "1",
            "k",
            "t",
            MapTransport(read_pages, stats={"appmsgstat": {"read_num": 3, "like_num": 1, "old_like_num": 9}}),
            CrawlOptions(min_reads=10),
        ).run()
        self.assertEqual(low[0].reason, "low_reads")
        self.assertIn("阅读量为3", low[0].message)
        self.assertEqual(parse_like_num({"appmsgstat": {"read_num": 3, "like_num": 1, "old_like_num": 9}}).like_num, 9)

    def test_download_writes_html_and_picture_page(self) -> None:
        url = "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=5&idx=1&sn=ggg"
        pages = {0: string_page([message("正文", url)], 10, 0)}
        html = '<html><head><meta property="og:title" content="正文"></head><body><div id="js_content"><p>hi</p></div></body></html>'
        with tempfile.TemporaryDirectory() as folder:
            rows = HistoryCrawler(
                "Abcd1234",
                "1",
                "k",
                "t",
                MapTransport(pages, articles={url: html}),
                CrawlOptions(download=True, out_dir=Path(folder)),
            ).run()
            self.assertEqual(rows[0].action, "download")
            self.assertTrue(Path(rows[0].saved_path).is_file())
            self.assertTrue((Path(folder) / "download_manifest.csv").is_file())
        picture = '<meta property="og:title" content="图片页">\ncdn_url: \'https://mmbiz.qpic.cn/a.jpg\','
        page, kind = select_article_html(picture)
        self.assertEqual(kind, "picture")
        self.assertIn("mmbiz.qpic.cn/a.jpg", page)
        album_body = compact(
            {
                "getalbum_resp": compact(
                    {
                        "continue_flag": "0",
                        "article_list": [
                            {
                                "title": "别名",
                                "link": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=9&idx=1&sn=sss",
                                "msg_id": "9",
                                "item_idx": "1",
                                "publish_time": 1700000000,
                            }
                        ],
                    }
                )
            }
        )
        found, can_continue = parse_album_response(album_body)
        self.assertEqual(found[0].title, "别名")
        self.assertIn("sn=sss", found[0].url)
        self.assertEqual(found[0].published_at, 1700000000)
        self.assertIs(can_continue, False)
        single, single_continue = parse_album_response(
            compact({"getalbum_resp": {"continue_flag": 1, "reverse_continue_flag": "0", "article_list": {"title": "单条", "url": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1&idx=1&sn=one"}}}),
            reverse=True,
        )
        self.assertEqual([item.title for item in single], ["单条"])
        self.assertIs(single_continue, False)
        self.assertEqual(show_type_name(8), "image")
        self.assertEqual(show_type_name(0), "article")
        self.assertEqual(show_type_name(None), "")
        article = ArticleRef("图片页", url, 1700000000, 1, 1, "main")
        with tempfile.TemporaryDirectory() as folder:
            written = write_article_files(Path(folder), article, page, save_markdown=True)
            self.assertEqual(len(written), 2)


class BizSessionAlbumTests(unittest.TestCase):
    def test_biz_at_end_of_query(self) -> None:
        url = "https://mp.weixin.qq.com/s?mid=1&__biz=Abcd1234"
        self.assertIsNone(extract_biz_legacy(url))
        self.assertEqual(extract_biz(url), "Abcd1234")
        self.assertEqual(normalize_content_url("http://mp.weixin.qq.com/s?__biz=Abcd1234&amp;mid=1&amp;idx=1&amp;sn=aa"), "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=1&idx=1&sn=aa")

    def test_scan_skips_video_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            keep = root / "config"
            skip = root / "video"
            keep.mkdir()
            skip.mkdir()
            (keep / "a.txt").write_text("https://mp.weixin.qq.com/s?uin=111&key=realKey&pass_ticket=ttt&x=1", encoding="utf-8")
            (skip / "b.txt").write_text("https://mp.weixin.qq.com/s?uin=222&key=hidden&pass_ticket=hhh&x=1", encoding="utf-8")
            found = scan_credentials([root])
        self.assertEqual([item.uin for item in found], ["111"])
        self.assertEqual(credentials_in_text("uin=1&key=k&pass_ticket=p&")[0].key, "k")

    def test_homepage_album_is_not_history(self) -> None:
        link = "https://mp.weixin.qq.com/mp/appmsgalbum?__biz=Abcd1234&album_id=99&msgid=1&itemidx=1"
        homepage = f'<html><a href="{link}">合集</a></html>'
        album = compact(
            {
                "getalbum_resp": {
                    "continue_flag": 0,
                    "article_list": [
                        {"title": "合集文", "url": "https://mp.weixin.qq.com/s?__biz=Abcd1234&mid=9&idx=1&sn=sss", "msgid": "9", "itemidx": "1"}
                    ],
                }
            }
        )

        def get_text(url: str, user_agent: str | None = None) -> str:
            if "homepage" in url:
                return homepage
            if "action=getalbum" in url:
                return album
            raise AssertionError(url)

        articles = crawl_album("https://mp.weixin.qq.com/mp/homepage?__biz=Abcd1234", biz="", uin="1", key="k", pass_ticket="t", get_text=get_text)
        self.assertEqual([item.title for item in articles], ["合集文"])


if __name__ == "__main__":
    unittest.main()
