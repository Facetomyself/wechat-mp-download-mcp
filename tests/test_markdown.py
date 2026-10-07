from __future__ import annotations

import unittest

from wechatdownload.article import html_to_markdown


PAGE = """<!DOCTYPE html><html><head>
<meta property="og:title" content="示例标题">
<script>var x = "<div id='js_content'>泄露脚本</div>";</script>
</head><body>
<h1 id="activity-name"><span>示例标题</span></h1>
<span>原创</span>
<div>在小说阅读器读本章</div>
<div class="rich_media_content" id="js_content" style="visibility: hidden;">
<p><span>&nbsp;</span></p>
<section>
<img data-src="https://mmbiz.qpic.cn/a.png" />
<h1><br></h1>
<blockquote><p>本文基于：<strong>APatch</strong><code>main@6ec140e</code><span>&nbsp;与&nbsp;</span><strong>KernelPatch</strong></p></blockquote>
<h2 data-heading="true"><span>01 开篇</span></h2>
<p><span>混在一起，</span><code>apd/</code><span>里二十多个</span><code>.rs</code><span>文件</span></p>
<p><code>before_openat</code><code>userd.c:1723</code></p>
<p><code>Option&lt;String&gt;</code></p>
<pre class="code-snippet__rust"><span><svg><ellipse></ellipse></svg></span><code><span>pub fn on_post_data_fs() {</span><br><span>    Ok(())</span></code></pre>
<figure><span><img data-src="https://mmbiz.qpic.cn/b.png" alt="图1：开机"></span><figcaption><span>图1：开机</span></figcaption></figure>
<ul class="list-paddingleft-1"><li><section><span>•&nbsp;</span><strong><code>post-fs-data</code></strong><span>时机</span></section></li></ul>
<ol><li><section><span>1. 校验</span><code>auth_superkey</code></section></li><li><section>2. 改写</section></li></ol>
<table><thead><tr><th>文件</th><th>角色</th></tr></thead><tbody><tr><td>event.rs</td><td>主角</td></tr></tbody></table>
<ul class="code-snippet__line-index"><li>1</li><li>2</li></ul>
</section>
</div>
<div>不喜欢</div>
</body></html>
"""


class MarkdownTests(unittest.TestCase):
    def test_article_body_keeps_structure_and_drops_chrome(self) -> None:
        text = html_to_markdown(PAGE)
        self.assertTrue(text.startswith("# 示例标题\n"))
        self.assertNotIn("原创", text)
        self.assertNotIn("小说阅读器", text)
        self.assertNotIn("不喜欢", text)
        self.assertNotIn("泄露脚本", text)
        self.assertNotIn("ellipse", text)
        self.assertIn("## 01 开篇", text)
        self.assertIn("混在一起，`apd/`里二十多个`.rs`文件", text)
        self.assertIn("`Option<String>`", text)
        self.assertIn("> 本文基于：**APatch** `main@6ec140e` 与 **KernelPatch**", text)
        self.assertIn("`before_openat` `userd.c:1723`", text)
        self.assertIn("```rust\npub fn on_post_data_fs() {\n    Ok(())\n```", text)
        self.assertIn("![](https://mmbiz.qpic.cn/a.png)", text)
        self.assertIn("![图1：开机](https://mmbiz.qpic.cn/b.png)", text)
        self.assertIn("图1：开机", text)
        self.assertIn("- `post-fs-data`时机", text)
        self.assertIn("1. 校验`auth_superkey`", text)
        self.assertIn("2. 改写", text)
        self.assertNotIn("- 1\n", text)
        self.assertIn("| 文件 | 角色 |", text)
        self.assertIn("| event.rs | 主角 |", text)
        self.assertLess(text.count("\n"), 40)

    def test_picture_page_uses_body_when_content_is_absent(self) -> None:
        page = (
            '<meta property="og:title" content="图片页">'
            '<html><body><h1>图片页</h1><p><img src="https://mmbiz.qpic.cn/a.jpg"></p></body></html>'
        )
        text = html_to_markdown(page)
        self.assertIn("# 图片页", text)
        self.assertIn("![](https://mmbiz.qpic.cn/a.jpg)", text)
        self.assertNotIn("<img", text)

    def test_lazy_media_math_and_background(self) -> None:
        page = """<html><head><meta property="og:title" content="卡片"></head><body>
        <div id="js_content">
          <img src="data:image/gif;base64,AAAA" data-src="https://mmbiz.qpic.cn/real.png">
          <section style="background-image:url(https://mmbiz.qpic.cn/bg.png)"><span>配图</span></section>
          <mpvoice name="开机说明" voice_encode_fileid="abcdef123456"></mpvoice>
          <iframe data-src="https://v.qq.com/txp/iframe/player.html"></iframe>
          <qqmusic music_name="夜曲" singer="周杰伦" audiourl="https://res.wx.qq.com/a.mp3"></qqmusic>
          <span class="katex"><span class="katex-html">E</span><annotation encoding="application/x-tex">E=mc^2</annotation></span>
          <p><del>旧结论</del></p>
          <section class="code-snippet__fix"><ul class="code-snippet__line-index"><li>1</li></ul>
            <pre class="code-snippet__python" data-lang="python"><code><span>counter(line)</span><br><span class="code-snippet__keyword">print</span><span>(1)</span></code></pre>
          </section>
        </div></body></html>"""
        text = html_to_markdown(page)
        self.assertIn("![](https://mmbiz.qpic.cn/real.png)", text)
        self.assertIn("![](https://mmbiz.qpic.cn/bg.png)", text)
        self.assertIn("[语音：开机说明](https://res.wx.qq.com/voice/getvoice?mediaid=abcdef123456)", text)
        self.assertIn("[视频：视频](https://v.qq.com/txp/iframe/player.html)", text)
        self.assertIn("[音乐：夜曲 - 周杰伦](https://res.wx.qq.com/a.mp3)", text)
        self.assertIn("$E=mc^2$", text)
        self.assertNotIn("katex-html", text)
        self.assertIn("~~旧结论~~", text)
        self.assertIn("```python\nprint(1)\n```", text)
        self.assertNotIn("- 1", text)


if __name__ == "__main__":
    unittest.main()
