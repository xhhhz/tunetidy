"""加密实现与 Node 内置 crypto 独立算出的参考值逐字节一致（已验证）。"""

from tunetidy.backends.web.crypto import eapi, eapi_decrypt_request, weapi

DATA = {"id": "123", "n": 100000, "s": 8, "name": "摇滚"}


def test_weapi_matches_node_reference() -> None:
    out = weapi(DATA, secret_key="abcdefghijklmnop")
    assert out["params"] == (
        "HpEZL8REUsvwiO4pOpBnVkNYyFuLuIhh7PqENo4otWS/OBOb4DkAm6mRvF8lC/iu"
        "2rm31DR9ndu6mLf92Yx6dzOtX56AIy74fRKe04Kf81Q="
    )
    assert out["encSecKey"] == (
        "d15a1683c992095d0c234c19966605c5c5964911268bbeda8cb8d08d834913e5"
        "9d53b32358903a121b5fca784c1f5ae44951fd02524df58ecc98e52cc7cf8689"
        "b42c2e93ddf05b0592512d87f5960467e2f086c018849d76014d323500e30f13"
        "ef4cafbb0cf5a66731a3f1776c75ca35d0062dac70a3e33245afabcf47938487"
    )


def test_weapi_random_key_differs() -> None:
    assert weapi(DATA)["encSecKey"] != weapi(DATA)["encSecKey"]


def test_eapi_matches_node_reference_and_roundtrips() -> None:
    out = eapi("/api/v6/playlist/detail", DATA)
    assert out["params"].startswith("9E44C61C7604F33F328DE9633B8B0E69")
    assert out["params"].endswith("462850A566E584")
    assert eapi_decrypt_request(out["params"]) == ("/api/v6/playlist/detail", DATA)
