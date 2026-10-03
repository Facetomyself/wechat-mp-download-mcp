from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from wechatdownload.models import Credential

# 常量里的原样正则。第三个字符类是 a-zA-z，和 4.6 一致。
URL_RE = re.compile(r"https?://[a-zA-z0-9:/?.&=+%_]+")
UIN_RE = re.compile(r"uin=(.*?)&")
KEY_RE = re.compile(r"key=(.*?)&")
TICKET_RE = re.compile(r"pass_ticket=([^&]*)")
POC_RE = re.compile(r"poc_token=([^&]*)")
POC_SID_RE = re.compile(r"poc_sid=([^;]+)")

# 常量表只有 skip_dirs 这个名字和「跳过聊天记录、视频、图片文件夹」的说明，
# 没有目录名字符串。下面这组是按这句话做的剪枝，不是逐字节还原。
INFERRED_SKIP_DIRS = {
    "msg",
    "message",
    "video",
    "image",
    "img",
    "cache",
    "filestorage",
}
MEDIA_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".mp4",
    ".mp3",
    ".silk",
    ".wxgf",
    ".exe",
    ".dll",
    ".pyd",
}
MAX_FILE_BYTES = 4_000_000


def default_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / "AppData" / "Roaming" / "Tencent" / "xwechat",
        home / "AppData" / "Roaming" / "Tencent" / "WeChat",
    ]
    if sys.platform == "darwin":
        roots.append(home / "Library" / "Containers" / "com.tencent.xinWeChat")
    return roots


def _decode_token(value: str) -> str:
    return unquote((value or "").strip())


def credentials_in_text(text: str, source: str = "", mtime: float = 0) -> list[Credential]:
    found: list[Credential] = []
    seen: set[tuple[str, str, str]] = set()
    pieces = URL_RE.findall(text or "")
    if "uin=" in (text or "") and not pieces:
        pieces = [text]
    for piece in pieces:
        sample = piece if piece.endswith("&") else piece + "&"
        uin_match = UIN_RE.search(sample)
        key_match = KEY_RE.search(sample)
        if not uin_match or not key_match:
            continue
        ticket_match = TICKET_RE.search(sample)
        poc_match = POC_RE.search(sample)
        cred = Credential(
            uin=_decode_token(uin_match.group(1)),
            key=_decode_token(key_match.group(1)),
            pass_ticket=_decode_token(ticket_match.group(1) if ticket_match else ""),
            poc_token=_decode_token(poc_match.group(1) if poc_match else ""),
            source=source,
            mtime=mtime,
        )
        if not cred.uin or not cred.key:
            continue
        ident = (cred.uin, cred.key, cred.pass_ticket)
        if ident in seen:
            continue
        seen.add(ident)
        found.append(cred)
    return found


def extract_poc_sid(set_cookie: str) -> str:
    match = POC_SID_RE.search(set_cookie or "")
    return match.group(1) if match else ""


def _skip_dir(name: str) -> bool:
    return name.lower() in INFERRED_SKIP_DIRS


def iter_candidate_files(roots: list[Path]):
    """os.scandir 遍历，剪枝后按 mtime 从新到旧交给调用方。"""
    files: list[tuple[float, Path]] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if _skip_dir(entry.name):
                            continue
                        stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    suffix = Path(entry.name).suffix.lower()
                    if suffix in MEDIA_SUFFIXES:
                        continue
                    stat = entry.stat()
                except OSError:
                    continue
                if stat.st_size <= 0 or stat.st_size > MAX_FILE_BYTES:
                    continue
                files.append((stat.st_mtime, Path(entry.path)))
    files.sort(key=lambda item: item[0], reverse=True)
    for mtime, path in files:
        yield mtime, path


def scan_credentials(roots: list[Path] | None = None, limit: int = 20) -> list[Credential]:
    found: list[Credential] = []
    seen: set[tuple[str, str, str]] = set()
    for mtime, path in iter_candidate_files(roots if roots is not None else default_roots()):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:1024]:
            continue
        text = raw.decode("utf-8", errors="ignore")
        for cred in credentials_in_text(text, source=str(path), mtime=mtime):
            ident = (cred.uin, cred.key, cred.pass_ticket)
            if ident in seen:
                continue
            seen.add(ident)
            found.append(cred)
            if len(found) >= limit:
                return found
    return found
