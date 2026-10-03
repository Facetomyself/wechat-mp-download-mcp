from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class SessionStore:
    """本机会话文件。调用方只向外返回 public()。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def update(self, **fields: object) -> dict:
        with self._lock:
            data = self.load()
            data.update(fields)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            return data

    def public(self) -> dict:
        data = self.load()
        uin = str(data.get("uin") or "")
        verified_at = data.get("verified_at") or None
        age = None
        if isinstance(verified_at, (int, float)) and verified_at > 0:
            age = max(0, int(time.time() - float(verified_at)))
        ready = bool(data.get("verified") and data.get("biz") and data.get("uin") and data.get("key"))
        return {
            "ready": ready,
            "biz": str(data.get("biz") or ""),
            "uin_hint": f"…{uin[-4:]}" if len(uin) >= 4 else "",
            "has_pass_ticket": bool(data.get("pass_ticket")),
            "has_poc_token": bool(data.get("poc_token")),
            "verified": bool(data.get("verified")),
            "verified_at": verified_at,
            "age_seconds": age,
            "source": str(data.get("source") or ""),
        }

    def require(self) -> dict:
        data = self.load()
        if not (data.get("verified") and data.get("biz") and data.get("uin") and data.get("key")):
            raise RuntimeError("会话无效。请先 prepare_account，在微信中打开确认链接，再 capture_session。")
        return data
