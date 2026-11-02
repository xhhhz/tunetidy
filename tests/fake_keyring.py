"""测试专用的内存钥匙串。tests/conftest.py 用 PYTHON_KEYRING_BACKEND 强制 keyring 使用它，
保证测试绝不会读到作者真实的登录凭据。"""

from __future__ import annotations

from keyring.backend import KeyringBackend


class FakeKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)
