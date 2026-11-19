import json

from wellgraph import progress


def test_report_writes_stages_and_render_shows_percent(tmp_path, monkeypatch):
    path = tmp_path / "p" / "progress.json"
    monkeypatch.setenv("WELLGRAPH_PROGRESS", str(path))
    monkeypatch.setattr(progress, "_state", {})
    monkeypatch.setattr(progress, "_last_write", 0.0)

    progress.report("documents", 10, 10, force=True)
    progress.report("extract", 50, 200, force=True)

    state = json.loads(path.read_text())
    assert state["stage"] == "extract" and state["stages"]["documents"]["done"] == 10
    state["stages"]["extract"]["started"] = state["stages"]["extract"]["updated"] - 10
    text = progress.render(state)
    assert "[x] Read reports: 10 of 10" in text
    assert "25.0%  50 of 200" in text and "5.0/s" in text and "left" in text
    assert "[ ] Second depth pass" in text


def test_report_does_nothing_without_the_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("WELLGRAPH_PROGRESS", raising=False)
    monkeypatch.setattr(progress, "_state", {})
    progress.report("extract", 1, 2, force=True)
    assert progress._state == {}


def test_finished_and_stalled_runs_are_named():
    base = {"started": 0.0, "updated": 100.0,
            "stages": {"documents": {"started": 0.0, "updated": 100.0, "done": 3, "total": 9}}}
    assert progress.render({**base, "stage": "done"}, now=100.0).startswith("Pipeline finished")
    assert "no update for 8 min" in progress.render({**base, "stage": "documents"}, now=600.0)
