import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.integrations.runtime import is_sender_allowed
from src.integrations.runtime import try_pair_sender


def test_is_sender_allowed_default_open_when_no_auth_config():
    assert is_sender_allowed({}, "ou_123") is True
    assert is_sender_allowed({"allowed_users": []}, "ou_123") is True


def test_is_sender_allowed_restricted_when_pairing_enabled():
    cfg = {"allowed_users": [], "pairing_code": "ABCD1234"}
    assert is_sender_allowed(cfg, "ou_123") is False


def test_is_sender_allowed_allowlist():
    cfg = {"allowed_users": ["ou_123"]}
    assert is_sender_allowed(cfg, "ou_123") is True
    assert is_sender_allowed(cfg, "ou_999") is False


def test_try_pair_sender_tolerates_formatting_noise():
    cfg = {"allowed_users": [], "pairing_code": "ABCD1234", "pairing_expires_at": 4102444800}
    r = try_pair_sender(cfg, "ou_abc", "ABCD 1234")
    assert r.paired is True
    assert "ou_abc" in cfg["allowed_users"]
