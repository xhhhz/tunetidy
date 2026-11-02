"""所有测试都在假钥匙串下运行。"""

import keyring

from tests.fake_keyring import FakeKeyring


def test_keyring_is_fake() -> None:
    assert isinstance(keyring.get_keyring(), FakeKeyring)
    assert keyring.get_password("tunetidy", "netease") is None
