import os

# 在导入任何会用到 keyring 的模块之前设置：测试一律用内存钥匙串，读不到真实凭据
FAKE_KEYRING = "tests.fake_keyring.FakeKeyring"
os.environ["PYTHON_KEYRING_BACKEND"] = FAKE_KEYRING

import json  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    """keyring 不是假的就立即终止整轮测试，避免任何测试碰到真实钥匙串。"""
    import keyring

    from tests.fake_keyring import FakeKeyring

    if not isinstance(keyring.get_keyring(), FakeKeyring):
        pytest.exit(f"测试必须使用假钥匙串（{FAKE_KEYRING}），当前是 {keyring.get_keyring()!r}", 2)


def load_fixture(name: str) -> Any:
    """读取 tests/fixtures/ 下的脱敏录制响应，如 load_fixture("netease/song_detail.json")。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
