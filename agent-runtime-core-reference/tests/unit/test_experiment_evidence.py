import json
from unittest.mock import AsyncMock

import pytest

from examples import evaluate, load


@pytest.mark.parametrize("profile,levels", [("overload", [1, 5, 10]), ("steady", [1, 2, 3])])
async def test_load_profile_preserves_preregistered_denominator(tmp_path, monkeypatch, profile, levels):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "TASK_STATUS.json").write_text(json.dumps({"run_id": "test"}))
    monkeypatch.setattr(load.DockerBackend, "availability", AsyncMock(return_value={"available": True}))
    await load.run(False, profile=profile)
    plan = json.loads((tmp_path / "reports/test/benchmarks/load_not_run.json").read_text())
    assert plan["profile"] == profile and plan["concurrency"] == levels
    assert plan["total"] == len(levels) * plan["rounds"] * plan["submissions_per_round"] == 270
    assert plan["admission_limit_per_principal"] == 3
    assert plan["rate_limit_per_minute"] == 30


@pytest.mark.parametrize("module,filename", [
    (load, "load_config.json"), (load, "load_raw_events.jsonl"),
    (evaluate, "model_config.json"), (evaluate, "not_run.json"),
])
async def test_existing_evidence_is_rejected_before_preflight(tmp_path, monkeypatch, module, filename):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "TASK_STATUS.json").write_text(json.dumps({"run_id": "test"}))
    out = tmp_path / "reports/test/benchmarks"
    out.mkdir(parents=True)
    prior = out / filename
    prior.write_text("immutable evidence")
    availability = AsyncMock()
    monkeypatch.setattr(module.DockerBackend, "availability", availability)
    with pytest.raises(RuntimeError, match="overwrite"):
        await module.run(False)
    assert prior.read_text() == "immutable evidence"
    availability.assert_not_awaited()


async def test_model_preflight_does_not_report_zero_load_samples(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "TASK_STATUS.json").write_text(json.dumps({
        "run_id": "test", "load_evidence_dir": "benchmarks/load-attempt-2"}))
    monkeypatch.setattr(evaluate.DockerBackend, "availability", AsyncMock(return_value={"available": True}))
    monkeypatch.setattr(evaluate, "qualified", lambda path: True)
    monkeypatch.setattr(evaluate.settings, "model_enabled", False)
    await evaluate.run(False)
    result = json.loads(capsys.readouterr().out)
    assert result["load_status"] == "SEPARATE_EXPERIMENT" and "actual_load_tasks" not in result
    plan = json.loads((tmp_path / "reports/test/benchmarks/not_run.json").read_text())
    assert plan["load_evidence_dir"] == "benchmarks/load-attempt-2"
