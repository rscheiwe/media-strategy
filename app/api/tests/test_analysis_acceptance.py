from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path

import pytest

from chat_api.casedata import build_case_db
from chat_api.chat.analysis import _get_case_data, _run_python_analysis, run_analysis


@pytest.fixture
def case_db(tmp_path, monkeypatch):
    path = build_case_db(Path(__file__).resolve().parents[1] / "case_data", tmp_path / "case.db")
    monkeypatch.setenv("APP_CASE_DB_PATH", str(path))
    return path


async def test_valid_analysis_returns_stdout_and_datasets_used(case_db):
    ctx = {"datasets": {}}
    fetched = _get_case_data({"dataset": "campaign_history", "filters": {"vertical": "QSR"}}, ctx)
    result = await _run_python_analysis({"question": "QSR CTR", "datasets": [fetched["dataset_id"]], "code": "df=data['d1']; x=df.groupby('product_id').agg({'clicks':'sum','impressions':'sum'}); print((x.clicks/x.impressions).to_dict())"}, ctx)
    assert result["success"] is True
    assert "P004" in result["stdout"] and "0.01148" in result["stdout"]
    assert result["datasets_used"] == ["d1"]


@pytest.mark.parametrize("code", ["def nope(:\n pass", "1 / 0"])
async def test_errors_return_one_line_without_traceback(case_db, code):
    result = await run_analysis(code, datasets={})
    assert result["success"] is False
    assert result["error"] and "\n" not in result["error"]
    assert "Traceback" not in result["error"] + result["stderr"]
    assert 'File "' not in result["error"] + result["stderr"]


async def test_timeout_kills_group_cleans_workspace_and_loop_stays_responsive(case_db):
    spawned = []
    task = asyncio.create_task(run_analysis("while True: pass", datasets={}, timeout=1.0, on_spawn=lambda pid, workspace: spawned.append((pid, workspace))))
    ticks = 0
    while not task.done():
        await asyncio.sleep(0.05); ticks += 1
    result = await task
    assert result["timed_out"] is True and ticks >= 5
    assert result["duration_ms"] < 3000
    pid, workspace = spawned[0]
    with pytest.raises(ProcessLookupError): os.killpg(pid, 0)
    assert not workspace.exists()


async def test_output_over_cap_is_truncated(case_db):
    result = await run_analysis("print('x' * 40000)", datasets={}, output_cap=32768)
    assert result["success"] is True and result["truncated"] is True
    assert len(result["stdout"].encode()) <= 32768


async def test_sandbox_has_no_data_connectivity_or_secrets(case_db, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    code = "import os\nprint('db' in globals(), 'query' in globals())\nprint(any('case.db' in str(v) or '.db' in str(v) for v in os.environ.values()))\nprint(os.environ.get('OPENAI_API_KEY'))\nprint(data)"
    result = await run_analysis(code, datasets={})
    assert result["success"] is True
    assert "must-not-leak" not in result["stdout"]
    assert "False False" in result["stdout"]
    assert result["stdout"].count("False") >= 3


async def test_cancel_kills_child(case_db):
    spawned = []
    task = asyncio.create_task(run_analysis("while True: pass", datasets={}, timeout=10, on_spawn=lambda pid, workspace: spawned.append((pid, workspace))))
    while not spawned: await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    pid, workspace = spawned[0]
    with pytest.raises(ProcessLookupError): os.killpg(pid, 0)
    assert not workspace.exists()


def test_output_is_complete_for_the_model_under_both_loops(case_db):
    code = """```python
import pandas as pd
wide = pd.DataFrame([{'campaign': 'QSR', 'impressions': 15000 / 9.0 * 1000, 'cpm': 18.0, 'ctr': 0.0123, 'in_view_rate': 0.748, 'ecpc': 1.46, 'vcpm': 24.06}])
(wide, wide)
```"""
    async def exercise():
        rendered = await run_analysis(code, datasets={})
        bare_sql = await run_analysis("SELECT product_id FROM products", datasets={})
        return rendered, bare_sql

    loops = [("asyncio", asyncio.run)]
    import uvloop
    loops.append(("uvloop", uvloop.run))
    for label, runner in loops:
        rendered, bare_sql = runner(exercise())
        assert rendered["success"] is True, label
        for column in ("campaign", "impressions", "cpm", "ctr", "in_view_rate", "ecpc", "vcpm"):
            assert column in rendered["stdout"], (label, rendered["stdout"])
        assert "..." not in rendered["stdout"]
        assert "e+" not in rendered["stdout"].lower()
        assert "1,666,667" in rendered["stdout"]
        assert bare_sql["success"] is False and "SyntaxError" in bare_sql["error"]


def test_get_case_data_catalog_filters_and_handles(case_db):
    ctx = {"datasets": {}}
    qsr = _get_case_data({"dataset": "campaign_history", "filters": {"vertical": "QSR", "product_id": ["P001", "P004"]}}, ctx)
    assert qsr["dataset_id"] == "d1" and qsr["row_count"] == len(ctx["datasets"]["d1"])
    root = Path(__file__).resolve().parents[1] / "case_data"
    expected = [row for row in csv.DictReader((root / "campaign_history.csv").open()) if row["vertical"] == "QSR" and row["product_id"] in {"P001", "P004"}]
    assert qsr["row_count"] == len(expected) and len(qsr["preview"]) <= 5
    assert sum(int(row["clicks"]) for row in ctx["datasets"]["d1"]) == sum(int(row["clicks"]) for row in expected)
    assert qsr["columns"] == ["product_id", "vertical", "impressions", "clicks", "viewable_impressions"]
    for bad in ({"dataset": "benchmarks", "filters": {}}, {"dataset": "products", "filters": {"vertical": "QSR"}}, {"dataset": "products", "filters": {"nope": "x"}}):
        result = _get_case_data(bad, ctx)
        assert "error" in result
    assert set(ctx["datasets"]) == {"d1"}


async def test_dataset_ids_resolve_within_a_turn_only(case_db):
    first, second = {"datasets": {}}, {"datasets": {}}
    dataset = _get_case_data({"dataset": "products", "filters": {}}, first)
    result = await _run_python_analysis({"question": "test", "code": "print(data)", "datasets": [dataset["dataset_id"]]}, second)
    assert result["success"] is False
    assert "get_case_data" in result["error"]
