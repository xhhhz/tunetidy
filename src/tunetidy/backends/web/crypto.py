"""网易云 weapi / eapi 请求加密。算法参照 api-enhanced，已与 Node 参考实现逐字节比对。"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
from typing import Any

from cryptography.hazmat.primitives import padding, serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_IV = b"0102030405060708"
_PRESET_KEY = b"0CoJUm6Qyw8W8jud"
_EAPI_KEY = b"e82ckenh8dichen8"
_EAPI_SEP = "-36cd479b6b5-"
_BASE62 = string.ascii_letters + string.digits
_PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDgtQn2JZ34ZC28NWYpAUd98iZ37BUrX/aKzmFbt7clFSs6sXqHauqKWqdtLkF2KexO40H1YTX8z2lSgBBOAxLsvaklV8k4cBFK9snQXE9/DDaFt6Rr7iVZMldczhC0JNgTz+SHXT6CBHuX3e9SdB1Ua44oncaTWz7OBGLbCiK45wIDAQAB
-----END PUBLIC KEY-----"""


def _load_rsa() -> tuple[int, int]:
    key = serialization.load_pem_public_key(_PUBLIC_KEY_PEM)
    assert isinstance(key, RSAPublicKey)
    nums = key.public_numbers()
    return nums.n, nums.e


_RSA_N, _RSA_E = _load_rsa()


def _pad(data: bytes) -> bytes:
    p = padding.PKCS7(128).padder()
    return p.update(data) + p.finalize()


def _unpad(data: bytes) -> bytes:
    u = padding.PKCS7(128).unpadder()
    return u.update(data) + u.finalize()


def _aes_cbc(data: bytes, key: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CBC(_IV)).encryptor()
    return enc.update(_pad(data)) + enc.finalize()


def _aes_ecb(data: bytes, key: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(_pad(data)) + enc.finalize()


def _aes_ecb_decrypt(data: bytes, key: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return _unpad(dec.update(data) + dec.finalize())


def _dumps(data: dict[str, Any]) -> str:
    # 与 JS 的 JSON.stringify 一致：无空格、中文不转义
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def weapi(data: dict[str, Any], secret_key: str | None = None) -> dict[str, str]:
    sk = secret_key or "".join(secrets.choice(_BASE62) for _ in range(16))
    first = base64.b64encode(_aes_cbc(_dumps(data).encode(), _PRESET_KEY))
    params = base64.b64encode(_aes_cbc(first, sk.encode())).decode()
    m = int.from_bytes(sk[::-1].encode(), "big")
    return {"params": params, "encSecKey": format(pow(m, _RSA_E, _RSA_N), "x").zfill(256)}


def eapi(path: str, data: dict[str, Any]) -> dict[str, str]:
    text = _dumps(data)
    digest = hashlib.md5(f"nobody{path}use{text}md5forencrypt".encode()).hexdigest()
    payload = f"{path}{_EAPI_SEP}{text}{_EAPI_SEP}{digest}".encode()
    return {"params": _aes_ecb(payload, _EAPI_KEY).hex().upper()}


def eapi_decrypt_request(params_hex: str) -> tuple[str, dict[str, Any]]:
    """还原 eapi 请求的 (path, data)，供测试使用。"""
    raw = _aes_ecb_decrypt(bytes.fromhex(params_hex), _EAPI_KEY).decode()
    path, text, _ = raw.split(_EAPI_SEP)
    data: dict[str, Any] = json.loads(text)
    return path, data
