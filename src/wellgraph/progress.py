"""Progress of a pipeline run, written to a small JSON file and shown as percentages.

    PYTHONPATH=src python -m wellgraph.progress [--file data/staging/progress.json] [--watch]

The pipeline writes the file when `WELLGRAPH_PROGRESS` names a path (the pipeline's
`--progress` option sets it). Each stage reports how many items are done out of how many;
this module prints the stages with percent, rate and time left.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_FILE = Path("data/staging/progress.json")
STAGES = ["documents", "extract", "depth_pass", "daily_reports", "core", "done"]
LABELS = {
    "documents": "Read reports",
    "extract": "Extract events (model calls)",
    "depth_pass": "Second depth pass (model calls)",
    "daily_reports": "Daily reports",
    "core": "Core data",
}

_state: dict[str, Any] = {}
_last_write = 0.0


def report(stage: str, done: int = 0, total: int = 0, *, force: bool = False) -> None:
    """Record progress of one stage. Cheap to call often: writes at most once a second."""
    global _last_write
    path = os.environ.get("WELLGRAPH_PROGRESS")
    if not path:
        return
    now = time.time()
    if not _state:
        _state.update({"started": now, "stages": {}})
    entry = _state["stages"].setdefault(stage, {"started": now})
    entry.update({"done": done, "total": total, "updated": now})
    _state.update({"stage": stage, "updated": now})
    if not force and now - _last_write < 1.0 and done < total:
        return
    _last_write = now
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(_state))
    tmp.replace(target)


def _duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {secs:02d} s"


def render(state: dict[str, Any], now: float | None = None) -> str:
    now = time.time() if now is None else now
    lines = []
    current = state.get("stage")
    for stage in STAGES[:-1]:
        entry = state.get("stages", {}).get(stage)
        label = LABELS[stage]
        if entry is None:
            lines.append(f"  [ ] {label}")
            continue
        done, total = entry.get("done", 0), entry.get("total", 0)
        finished = stage != current or current == "done"
        if finished:
            took = _duration(entry["updated"] - entry["started"])
            count = f"{done:,} of {total:,}" if total else "done"
            lines.append(f"  [x] {label}: {count} in {took}")
            continue
        percent = 100.0 * done / total if total else 0.0
        elapsed = entry["updated"] - entry["started"]
        rate = done / elapsed if elapsed > 0 else 0.0
        left = f", about {_duration((total - done) / rate)} left" if rate > 0 and total else ""
        bar = "#" * int(percent // 5) + "." * (20 - int(percent // 5))
        lines.append(f"  [>] {label}: {bar} {percent:5.1f}%  {done:,} of {total:,}"
                     f"  ({rate:.1f}/s{left})")
    head = "finished" if current == "done" else "running"
    if current != "done" and now - state.get("updated", now) > 120:
        head = f"no update for {_duration(now - state['updated'])}"
    total_time = _duration(state.get("updated", now) - state.get("started", now))
    return f"Pipeline {head}, {total_time} so far\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=os.environ.get("WELLGRAPH_PROGRESS", str(DEFAULT_FILE)))
    parser.add_argument("--watch", action="store_true", help="Refresh every 5 seconds.")
    args = parser.parse_args(argv)
    path = Path(args.file)
    while True:
        if path.is_file():
            state = json.loads(path.read_text())
            text = render(state)
        else:
            state, text = {}, f"No progress file at {path} yet."
        if not args.watch:
            print(text)
            return 0
        sys.stdout.write("\033[2J\033[H" + text + "\n")
        sys.stdout.flush()
        if state.get("stage") == "done":
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
