import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.integrations.connectors.feishu import FeishuConnector


def test_extract_sender_id_prefers_open_id():
    sender = {
        "sender_id": {
            "open_id": "ou_xxx",
            "user_id": "u_xxx",
            "union_id": "on_xxx",
        }
    }
    assert FeishuConnector._extract_sender_id(sender) == "ou_xxx"


def test_extract_sender_id_fallback_to_user_or_union():
    sender_with_user = {
        "sender_id": {
            "open_id": "",
            "user_id": "u_xxx",
            "union_id": "on_xxx",
        }
    }
    sender_with_union = {
        "sender_id": {
            "open_id": "",
            "user_id": "",
            "union_id": "on_xxx",
        }
    }
    assert FeishuConnector._extract_sender_id(sender_with_user) == "u_xxx"
    assert FeishuConnector._extract_sender_id(sender_with_union) == "on_xxx"


def test_extract_sender_id_returns_empty_when_missing():
    assert FeishuConnector._extract_sender_id({}) == ""
    assert FeishuConnector._extract_sender_id({"sender_id": {}}) == ""
    assert FeishuConnector._extract_sender_id({"sender_id": "invalid"}) == ""
