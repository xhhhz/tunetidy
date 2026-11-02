"""扫码后端的登录态：Cookie 与设备 id。优先存系统钥匙串，不可用时存仅本人可读的文件。"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

KEYRING_SERVICE = "tunetidy"
KEYRING_USER = "netease-web"


@dataclass
class Session:
    cookies: dict[str, str] = field(default_factory=dict)
    device_id: str = field(default_factory=lambda: secrets.token_hex(26).upper())
    # 登录用户的 id 和昵称：只随登录态存在本机，供本地 pre-commit hook 防止误提交个人信息
    user_id: str | None = None
    nickname: str | None = None

    @property
    def logged_in(self) -> bool:
        return bool(self.cookies.get("MUSIC_U"))

    def to_json(self) -> str:
        return json.dumps(
            {
                "cookies": self.cookies,
                "device_id": self.device_id,
                "user_id": self.user_id,
                "nickname": self.nickname,
            }
        )

    @classmethod
    def from_json(cls, text: str) -> Session:
        d = json.loads(text)
        return cls(
            cookies=dict(d.get("cookies", {})),
            device_id=str(d["device_id"]),
            user_id=d.get("user_id"),
            nickname=d.get("nickname"),
        )


class SessionStore(Protocol):
    def load(self) -> Session: ...
    def save(self, session: Session) -> None: ...
    def clear(self) -> None: ...


class KeyringStore:
    """系统钥匙串；钥匙串不可用时退回 fallback 文件（权限 0600）。"""

    def __init__(self, fallback: Path) -> None:
        self.fallback = fallback

    def _keyring(self) -> Any:
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring

            if isinstance(keyring.get_keyring(), FailKeyring):
                return None
            return keyring
        except Exception:  # noqa: BLE001 - 钥匙串不可用时退回文件
            return None

    def load(self) -> Session:
        kr = self._keyring()
        if kr is not None:
            try:
                text = kr.get_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:  # noqa: BLE001
                text = None
            if text:
                return Session.from_json(text)
        if self.fallback.exists():
            return Session.from_json(self.fallback.read_text(encoding="utf-8"))
        return Session()

    def save(self, session: Session) -> None:
        kr = self._keyring()
        if kr is not None:
            try:
                kr.set_password(KEYRING_SERVICE, KEYRING_USER, session.to_json())
                return
            except Exception:  # noqa: BLE001
                pass
        self.fallback.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.fallback, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(session.to_json())
        self.fallback.chmod(0o600)

    def clear(self) -> None:
        kr = self._keyring()
        if kr is not None:
            # 本来就没有也算清除成功
            with contextlib.suppress(Exception):
                kr.delete_password(KEYRING_SERVICE, KEYRING_USER)
        self.fallback.unlink(missing_ok=True)


class MemoryStore:
    """测试用。"""

    def __init__(self, session: Session | None = None) -> None:
        self.session = session or Session()

    def load(self) -> Session:
        return self.session

    def save(self, session: Session) -> None:
        self.session = session

    def clear(self) -> None:
        self.session = Session(device_id=self.session.device_id)
