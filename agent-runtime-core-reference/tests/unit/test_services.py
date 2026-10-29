import json
from pathlib import Path

import pytest

from examples import services


def test_process_inspection_permission_failure_is_unknown(monkeypatch):
    def denied(self):
        raise PermissionError("different group session")
    monkeypatch.setattr(Path, "readlink", denied)
    assert services.owned(123) is None


def test_unknown_service_is_not_stopped_or_overwritten(tmp_path, monkeypatch):
    state = tmp_path / "services.json"
    state.write_text(json.dumps([{"pid": 123, "port": 18081}]))
    monkeypatch.setattr(services, "STATE", state)
    monkeypatch.setattr(services, "owned", lambda pid: None)
    with pytest.raises(RuntimeError, match="UNKNOWN"):
        services.stop()
    assert json.loads(state.read_text())[0]["stopped"] is None
    with pytest.raises(RuntimeError, match="UNKNOWN"):
        services.start()
    assert json.loads(state.read_text())[0]["pid"] == 123
