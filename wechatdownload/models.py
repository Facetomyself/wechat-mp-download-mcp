from __future__ import annotations

from dataclasses import dataclass, field


def as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


@dataclass
class ArticleRef:
    title: str
    url: str
    published_at: int | None
    copyright_stat: int | None
    copyright_type: int | None
    source: str
    item_show_type: int | None = None
    msg_id: str = ""
    item_idx: str = ""

    @property
    def identity(self) -> str:
        if self.url:
            return self.url
        return f"empty:{self.source}:{self.msg_id}:{self.item_idx}:{self.title}"


@dataclass
class HistoryPage:
    items: list[dict]
    next_offset: int | None
    can_continue: bool | None
    ret: object = 0
    errmsg: str = ""
    offset: int = 0


@dataclass
class ParserDiff:
    legacy_error: str | None
    legacy_ids: list[str] = field(default_factory=list)
    fixed_ids: list[str] = field(default_factory=list)
    only_fixed: list[str] = field(default_factory=list)
    only_legacy: list[str] = field(default_factory=list)
    next_offset: int | None = None
    can_continue: bool | None = None
    fixed_error: str | None = None


@dataclass
class FilterOutcome:
    action: str
    reason: str = ""
    message: str = ""


@dataclass
class ReadStats:
    read_num: int
    like_num: int
    share_num: int


@dataclass
class CrawlRow:
    kind: str
    action: str
    reason: str = ""
    title: str = ""
    url: str = ""
    source: str = ""
    published_at: int | None = None
    offset: int | None = None
    message: str = ""
    saved_path: str = ""


@dataclass
class Credential:
    uin: str
    key: str
    pass_ticket: str
    poc_token: str = ""
    source: str = ""
    mtime: float = 0
