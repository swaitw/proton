from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path
from typing import Optional


def _first_existing(paths: list[Optional[str]]) -> Optional[str]:
    for p in paths:
        if not p:
            continue
        try:
            if Path(p).exists():
                return p
        except Exception:
            continue
    return None


def ensure_ssl_certs() -> Optional[str]:
    if os.getenv("SSL_CERT_FILE"):
        return os.getenv("SSL_CERT_FILE")
    if sys.platform == "darwin":
        use_truststore = os.getenv("PROTON_SSL_TRUSTSTORE", "1").strip().lower() not in ("0", "false", "no", "off")
        if use_truststore:
            try:
                import truststore  # type: ignore

                truststore.inject_into_ssl()
                return "truststore"
            except Exception:
                pass
        candidate = _first_existing(["/etc/ssl/cert.pem"])
        if candidate:
            os.environ["SSL_CERT_FILE"] = candidate
            return candidate
    paths = ssl.get_default_verify_paths()
    candidate = _first_existing([paths.cafile, paths.openssl_cafile])
    if candidate:
        os.environ["SSL_CERT_FILE"] = candidate
        return candidate
    try:
        import certifi  # type: ignore

        candidate = certifi.where()
        if candidate and Path(candidate).exists():
            os.environ["SSL_CERT_FILE"] = candidate
            return candidate
    except Exception:
        pass
    candidate = _first_existing(
        [
            "/etc/ssl/certs/ca-certificates.crt",
            "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
            "/etc/ssl/ca-bundle.pem",
            "/etc/ssl/cert.pem",
            "/etc/pki/tls/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
            "/opt/homebrew/etc/openssl@3/cert.pem",
            "/usr/local/etc/openssl@1.1/cert.pem",
            "/opt/homebrew/etc/openssl@1.1/cert.pem",
        ]
    )
    if candidate:
        os.environ["SSL_CERT_FILE"] = candidate
        return candidate
    return None
