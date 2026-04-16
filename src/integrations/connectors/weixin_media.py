from __future__ import annotations

import base64
import hashlib
import mimetypes
import secrets
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote

import aiohttp

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    _CRYPTO_AVAILABLE = True
except Exception:
    default_backend = None  # type: ignore[assignment]
    Cipher = None  # type: ignore[assignment]
    algorithms = None  # type: ignore[assignment]
    modes = None  # type: ignore[assignment]
    _CRYPTO_AVAILABLE = False

WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"

MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5


def crypto_available() -> bool:
    return _CRYPTO_AVAILABLE


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(_pkcs7_pad(plaintext)) + encryptor.finalize()


def _aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def _aes_padded_size(size: int) -> int:
    return ((size + 1 + 15) // 16) * 16


def _cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def _cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


def _parse_aes_key(aes_key_b64: str) -> bytes:
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")


async def get_upload_url(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    media_type: int,
    filekey: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{EP_GET_UPLOAD_URL}"
    headers = {
        "Content-Type": "application/json",
        "iLink-App-Id": "ilinkai",
        "iLink-App-ClientVersion": "1",
        "iLink-Bot-Token": token,
    }
    payload = {
        "filekey": filekey,
        "media_type": media_type,
        "to_user_id": to_user_id,
        "rawsize": rawsize,
        "rawfilemd5": rawfilemd5,
        "filesize": filesize,
        "no_need_thumb": True,
        "aeskey": aeskey_hex,
    }
    async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        raw = await resp.text()
        if not resp.ok:
            raise RuntimeError(f"getuploadurl HTTP {resp.status}: {raw[:200]}")
        return await resp.json()


async def upload_ciphertext(session: aiohttp.ClientSession, *, ciphertext: bytes, upload_url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=120)
    async with session.post(upload_url, data=ciphertext, headers={"Content-Type": "application/octet-stream"}, timeout=timeout) as response:
        if response.status == 200:
            encrypted_param = response.headers.get("x-encrypted-param")
            if encrypted_param:
                await response.read()
                return encrypted_param
            raw = await response.text()
            raise RuntimeError(f"cdn upload missing x-encrypted-param: {raw[:200]}")
        raw = await response.text()
        raise RuntimeError(f"cdn upload HTTP {response.status}: {raw[:200]}")


async def download_and_decrypt_media(
    session: aiohttp.ClientSession,
    *,
    cdn_base_url: str,
    encrypted_query_param: Optional[str],
    aes_key_b64: Optional[str],
    full_url: Optional[str],
    timeout_seconds: float = 60.0,
) -> bytes:
    if encrypted_query_param:
        url = _cdn_download_url(cdn_base_url, encrypted_query_param)
    elif full_url:
        url = full_url
    else:
        raise RuntimeError("media missing url")
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as resp:
        resp.raise_for_status()
        raw = await resp.read()
    if aes_key_b64:
        raw = _aes128_ecb_decrypt(raw, _parse_aes_key(aes_key_b64))
    return raw


def outbound_media_builder(path_or_name: str) -> Tuple[int, int]:
    mime = mimetypes.guess_type(path_or_name)[0] or "application/octet-stream"
    if mime.startswith("image/"):
        return MEDIA_IMAGE, ITEM_IMAGE
    if mime.startswith("video/"):
        return MEDIA_VIDEO, ITEM_VIDEO
    if mime.startswith("audio/") or path_or_name.endswith(".silk"):
        return MEDIA_VOICE, ITEM_VOICE
    return MEDIA_FILE, ITEM_FILE


def build_media_item(*, item_type: int, encrypted_query_param: str, aes_key_for_api: str, ciphertext_size: int, filename: str, rawfilemd5: str) -> Dict[str, Any]:
    media = {"encrypt_query_param": encrypted_query_param, "aes_key": aes_key_for_api, "encrypt_type": 1}
    if item_type == ITEM_IMAGE:
        return {"type": ITEM_IMAGE, "image_item": {"media": media, "mid_size": ciphertext_size}}
    if item_type == ITEM_VIDEO:
        return {"type": ITEM_VIDEO, "video_item": {"media": media, "video_size": ciphertext_size, "video_md5": rawfilemd5}}
    if item_type == ITEM_VOICE:
        return {"type": ITEM_VOICE, "voice_item": {"media": media, "playtime": 0}}
    return {
        "type": ITEM_FILE,
        "file_item": {
            "file_name": filename,
            "file_size": ciphertext_size,
            "media": media,
        },
    }


async def prepare_weixin_upload(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    token: str,
    to_user_id: str,
    plaintext: bytes,
    filename: str,
    cdn_base_url: str,
) -> Dict[str, Any]:
    if not _CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography not installed")
    media_type, item_type = outbound_media_builder(filename)
    filekey = secrets.token_hex(16)
    aes_key = secrets.token_bytes(16)
    rawsize = len(plaintext)
    rawfilemd5 = hashlib.md5(plaintext).hexdigest()
    upload_response = await get_upload_url(
        session,
        base_url=base_url,
        token=token,
        to_user_id=to_user_id,
        media_type=media_type,
        filekey=filekey,
        rawsize=rawsize,
        rawfilemd5=rawfilemd5,
        filesize=_aes_padded_size(rawsize),
        aeskey_hex=aes_key.hex(),
    )
    upload_param = str(upload_response.get("upload_param") or "")
    upload_full_url = str(upload_response.get("upload_full_url") or "")
    ciphertext = _aes128_ecb_encrypt(plaintext, aes_key)
    if upload_full_url:
        upload_url = upload_full_url
    elif upload_param:
        upload_url = _cdn_upload_url(cdn_base_url, upload_param, filekey)
    else:
        raise RuntimeError("getuploadurl missing upload url")
    encrypted_query_param = await upload_ciphertext(session, ciphertext=ciphertext, upload_url=upload_url)
    aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
    return {
        "item": build_media_item(
            item_type=item_type,
            encrypted_query_param=encrypted_query_param,
            aes_key_for_api=aes_key_for_api,
            ciphertext_size=len(ciphertext),
            filename=filename,
            rawfilemd5=rawfilemd5,
        ),
        "raw_md5": rawfilemd5,
        "raw_size": rawsize,
    }

