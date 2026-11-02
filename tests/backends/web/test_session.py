import stat
from pathlib import Path
from typing import Any

import pytest

from tunetidy.backends.web import session as session_mod
from tunetidy.backends.web.session import KeyringStore, Session


class BrokenKeyring:
    def get_password(self, *_: Any) -> str:
        raise RuntimeError("locked")

    def set_password(self, *_: Any) -> None:
        raise RuntimeError("locked")

    def delete_password(self, *_: Any) -> None:
        raise RuntimeError("locked")


class DictKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, s: str, u: str) -> str | None:
        return self.data.get((s, u))

    def set_password(self, s: str, u: str, v: str) -> None:
        self.data[(s, u)] = v

    def delete_password(self, s: str, u: str) -> None:
        del self.data[(s, u)]


def test_roundtrip_via_keyring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kr = DictKeyring()
    monkeypatch.setattr(KeyringStore, "_keyring", lambda self: kr)
    store = KeyringStore(tmp_path / "cred.json")
    s = Session(cookies={"MUSIC_U": "u"}, device_id="X")
    store.save(s)
    assert not (tmp_path / "cred.json").exists()  # 钥匙串可用时不落盘
    assert store.load() == s
    store.clear()
    assert store.load().cookies == {}


def test_falls_back_to_private_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(KeyringStore, "_keyring", lambda self: BrokenKeyring())
    path = tmp_path / "sub" / "cred.json"
    store = KeyringStore(path)
    s = Session(cookies={"MUSIC_U": "u"}, device_id="X")
    store.save(s)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load() == s
    store.clear()
    assert not path.exists()


def test_no_keyring_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(KeyringStore, "_keyring", lambda self: None)
    store = KeyringStore(tmp_path / "cred.json")
    assert store.load().cookies == {}
    assert not Session().logged_in
    assert Session(cookies={"MUSIC_U": "u"}).logged_in
    assert session_mod.Session.from_json(Session(device_id="A").to_json()).device_id == "A"
