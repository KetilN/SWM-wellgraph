"""Progress of a pipeline run, written to a small JSON file and shown as percentages.

    PYTHONPATH=src python -m wellgraph.progress [--file data/staging/progress.json] [--watch]
    PYTHONPATH=src python -m wellgraph.progress --serve 7861 [--log data/staging/run.log]

`--serve` opens a small dashboard (one page, standard library only) on that port.

The pipeline writes the file when `WELLGRAPH_PROGRESS` names a path (the pipeline's
`--progress` option sets it). Each stage reports how many items are done out of how many;
this module prints the stages with percent, rate and time left.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_FILE = Path("data/staging/progress.json")
STAGES = ["backbone", "documents", "extract", "depth_pass", "daily_reports", "core", "done"]
LABELS = {
    "backbone": "Backbone tables (SODIR)",
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


class _Sampler:
    """Keeps recent (time, items done) samples so the dashboard can show a rate over time."""

    def __init__(self, path: Path, log: Path | None, keep: int = 720, every: float = 5.0):
        self.path, self.log, self.keep, self.every = path, log, keep, every
        self.samples: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def run(self) -> None:
        while True:
            state = self.state()
            entry = state.get("stages", {}).get(state.get("stage"), {})
            sample = {"t": time.time(), "stage": state.get("stage"), "done": entry.get("done")}
            with self.lock:
                # rate over about half a minute: results arrive in bursts
                previous = self.samples[-6] if len(self.samples) >= 6 else (
                    self.samples[0] if self.samples else None)
                same = previous and previous["stage"] == sample["stage"] and sample["done"] is not None
                sample["rate"] = (
                    max(0.0, (sample["done"] - previous["done"]) / (sample["t"] - previous["t"]))
                    if same and previous["done"] is not None else None
                )
                self.samples = [*self.samples, sample][-self.keep:]
            time.sleep(self.every)

    def payload(self) -> dict[str, Any]:
        now, state = time.time(), self.state()
        with self.lock:
            history = list(self.samples)
        stage = state.get("stage")
        recent = [s for s in history if s["stage"] == stage and s["done"] is not None
                  and now - s["t"] <= 60]
        rate = 0.0
        if len(recent) > 1 and recent[-1]["t"] > recent[0]["t"]:
            rate = max(0.0, (recent[-1]["done"] - recent[0]["done"]) / (recent[-1]["t"] - recent[0]["t"]))
        failed = None
        if self.log is not None and self.log.is_file():
            with open(self.log, encoding="utf-8", errors="replace") as fh:
                failed = sum(1 for line in fh if "call failed" in line)
        alive = subprocess.run(["pgrep", "-f", "wellgraph.pipeline"], capture_output=True,
                               check=False).stdout.strip() != b""
        return {
            "now": now, "state": state, "order": STAGES[:-1], "labels": LABELS, "rate": rate,
            "history": [{"t": s["t"], "rate": s["rate"]} for s in history],
            "failed": failed, "alive": alive,
            "free_gb": shutil.disk_usage(self.path.parent if self.path.parent.is_dir() else ".").free / 1e9,
            "note": "Failed calls are skipped pages; a rerun picks them up from the reply cache.",
        }


def serve(path: Path, port: int, log: Path | None = None) -> None:
    """Serve the dashboard page and its JSON on localhost."""
    sampler = _Sampler(path, log)
    threading.Thread(target=sampler.run, daemon=True).start()
    page = (Path(__file__).parent / "progress_page.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/progress"):
                body, kind = json.dumps(sampler.payload()).encode(), "application/json"
            elif self.path in ("/", "/index.html"):
                body, kind = page, "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # quiet
            return

    print(f"Progress dashboard on http://localhost:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=os.environ.get("WELLGRAPH_PROGRESS", str(DEFAULT_FILE)))
    parser.add_argument("--watch", action="store_true", help="Refresh every 5 seconds.")
    parser.add_argument("--serve", type=int, metavar="PORT", help="Serve a dashboard page.")
    parser.add_argument("--log", help="Pipeline log; failed model calls are counted from it.")
    args = parser.parse_args(argv)
    path = Path(args.file)
    if args.serve:
        serve(path, args.serve, Path(args.log) if args.log else None)
        return 0
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
