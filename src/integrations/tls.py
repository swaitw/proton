from __future__ import annotations

import os
import ssl
from typing import Any, Dict, Optional, Union

import aiohttp


def _ca_file_from_env() -> Optional[str]:
    return os.getenv("SSL_CERT_FILE") or os.getenv("REQUESTS_CA_BUNDLE")


def _ssl_param(config: Dict[str, Any]) -> Union[None, bool, ssl.SSLContext]:
    verify_ssl = config.get("verify_ssl", True)
    if verify_ssl is False:
        return False
    ca_file = config.get("ca_file") or _ca_file_from_env()
    if ca_file:
        try:
            return ssl.create_default_context(cafile=str(ca_file))
        except Exception:
            return None
    return None


def create_aiohttp_session(config: Dict[str, Any]) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(ssl=_ssl_param(config))
    return aiohttp.ClientSession(trust_env=True, connector=connector)

