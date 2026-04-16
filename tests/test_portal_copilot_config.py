import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.portal import service as portal_service_module
from src.storage import persistence as persistence_module


def _reset_globals(monkeypatch, tmp_path):
    monkeypatch.setenv("PROTON_STORAGE_TYPE", "file")
    monkeypatch.setenv("PROTON_STORAGE_PATH", str(tmp_path))
    persistence_module._storage_manager = None
    portal_service_module._global_portal_manager = None
    portal_service_module._global_trajectory_pool = None


def test_default_portal_uses_copilot_internal_config_for_api_key(tmp_path, monkeypatch):
    _reset_globals(monkeypatch, tmp_path)

    class FakeCopilot:
        def get_internal_config(self):
            return {
                "provider": "zhipu",
                "model": "glm-4",
                "api_key": "copilot-secret-key",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
            }

    import src.copilot as copilot_module

    monkeypatch.setattr(copilot_module, "get_copilot_service", lambda: FakeCopilot())

    async def _run():
        mgr = portal_service_module.get_portal_manager()
        cfg = await mgr.ensure_default_portal()
        assert cfg.provider == "zhipu"
        assert cfg.model == "glm-4"
        assert cfg.api_key == "copilot-secret-key"
        assert cfg.base_url == "https://open.bigmodel.cn/api/paas/v4"

    asyncio.run(_run())
