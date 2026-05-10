#!/usr/bin/env python3
"""Soak orchestrator for sniper-executor (Paper Operator Release gate #7).

Run this manually before major releases (e.g. on a staging server) to prove
that the executor's resident-set-size does not drift more than a small
percentage over a long sustained run. The script:

  1. Either spawns ``sniper-executor`` as a child process or attaches to an
     already-running PID (via ``--pid`` / ``--target``).
  2. Periodically samples the executor's RSS (via ``/proc/<pid>/status`` on
     Linux, falling back to ``ps -o rss= -p <pid>`` for portability).
  3. Writes every sample to a CSV log immediately (crash-safe partial data).
  4. At the end of the run, smooths the head/tail samples (default 5-minute
     windows), computes the drift percentage, and exits 0 or 1 based on
     ``--max-drift-pct``.

Cron / unattended:
  ``python3 -m soak.run_72h_paper --hours 72 --target sniper-executor \
       --out-csv /var/log/soak/$(date +%F).csv --max-drift-pct 5.0``

Manual / quick local check:
  ``python3 tools/soak/run_72h_paper.py --hours 0.05 --pid $(pgrep sniper-executor)``

Stdlib only — no psutil, so the script works on bare staging hosts without
a virtualenv.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Optional

# Soak runs are long; the executor sometimes idles before warming up; we use a
# smoothing window over the *first* and *last* samples instead of comparing
# the very first sample to the very last. This also tolerates GC sawtooth.
DEFAULT_SMOOTH_WINDOW_SECS = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Sample model
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Sample:
    """A single RSS reading at ``elapsed_secs`` since soak start."""

    elapsed_secs: float
    rss_kb: int


# ---------------------------------------------------------------------------
# RSS reading
# ---------------------------------------------------------------------------

def read_rss_kb(pid: int) -> Optional[int]:
    """Read RSS in KB for the given PID. Returns ``None`` if pid is gone.

    Tries ``/proc/<pid>/status`` first (Linux, no fork) and falls back to
    ``ps -o rss= -p <pid>`` (macOS / unusual /proc layouts).
    """
    proc_status = Path(f"/proc/{pid}/status")
    if proc_status.exists():
        try:
            with proc_status.open("r", encoding="ascii", errors="replace") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        # `VmRSS:    123456 kB`
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            return int(parts[1])
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return None

    ps_path = shutil.which("ps")
    if ps_path is None:
        return None
    try:
        result = subprocess.run(
            [ps_path, "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = result.stdout.strip()
    if not out or not out.isdigit():
        return None
    return int(out)


def find_pid(target: str) -> Optional[int]:
    """Resolve a process name to a PID via ``pgrep -f -o``.

    Returns the *oldest* matching PID — when sniper-executor is restarted,
    the orchestrator should follow the original process, not whichever PID
    happens to start last.
    """
    pgrep_path = shutil.which("pgrep")
    if pgrep_path is None:
        return None
    try:
        result = subprocess.run(
            [pgrep_path, "-f", "-o", target],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = result.stdout.strip()
    if not out:
        return None
    first = out.splitlines()[0].strip()
    return int(first) if first.isdigit() else None


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def _select_window(samples: Sequence[Sample], start: float, end: float) -> list[Sample]:
    return [s for s in samples if start <= s.elapsed_secs <= end]


def compute_drift_pct(
    samples: Sequence[Sample],
    smooth_window_secs: float = DEFAULT_SMOOTH_WINDOW_SECS,
) -> Optional[float]:
    """Return percent drift between the smoothed head and smoothed tail.

    ``head_avg = mean(samples[t in [0, window]])``
    ``tail_avg = mean(samples[t in [last-window, last]])``
    ``drift = (tail_avg - head_avg) / head_avg * 100``

    Returns ``None`` when there are not enough samples to span the smoothing
    window on both ends. A negative drift (tail < head) is reported as-is so
    the caller can distinguish "stable" from "leaking" from "shrinking".
    """
    if len(samples) < 2:
        return None
    last_t = samples[-1].elapsed_secs
    if last_t < smooth_window_secs * 2:
        # Run was too short to give independent head/tail windows. Fall back to
        # whole-run mean of first and last halves.
        midpoint = last_t / 2.0
        head_window = _select_window(samples, 0.0, midpoint)
        tail_window = _select_window(samples, midpoint, last_t)
    else:
        head_window = _select_window(samples, 0.0, smooth_window_secs)
        tail_window = _select_window(samples, last_t - smooth_window_secs, last_t)
    if not head_window or not tail_window:
        return None
    head_avg = statistics.fmean(s.rss_kb for s in head_window)
    tail_avg = statistics.fmean(s.rss_kb for s in tail_window)
    if head_avg <= 0:
        return None
    return (tail_avg - head_avg) / head_avg * 100.0


def decide_exit(drift_pct: Optional[float], max_drift_pct: float) -> int:
    """Map a drift measurement to an exit code.

    - ``drift_pct is None``: not enough data, fail conservatively (1).
    - ``drift_pct > max_drift_pct``: leak detected (1).
    - otherwise: stable (0).

    A *negative* drift is always treated as healthy, even if its magnitude
    exceeds the threshold — the executor shrinking its working set is not a
    leak signal worth aborting a release for.
    """
    if drift_pct is None:
        return 1
    if drift_pct > max_drift_pct:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class OrchestratorConfig:
    pid: Optional[int]
    target: Optional[str]
    duration_secs: float
    sample_every_secs: float
    smooth_window_secs: float
    max_drift_pct: float
    out_csv: Optional[Path]


class Orchestrator:
    """Stateful sample-loop driver. Designed for both real and faked clocks."""

    def __init__(
        self,
        config: OrchestratorConfig,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
        rss_reader=read_rss_kb,
        stdout=sys.stdout,
    ) -> None:
        self.config = config
        self._clock = clock
        self._sleep = sleep
        self._rss_reader = rss_reader
        self._stdout = stdout
        self._stop_requested = False
        self.samples: list[Sample] = []
        self._csv_writer: Optional[csv.writer] = None
        self._csv_handle = None

    # public API ---------------------------------------------------------

    def request_stop(self) -> None:
        self._stop_requested = True

    def resolve_pid(self) -> int:
        if self.config.pid is not None:
            return self.config.pid
        if self.config.target is None:
            raise ValueError("either --pid or --target must be provided")
        pid = find_pid(self.config.target)
        if pid is None:
            raise RuntimeError(
                f"could not resolve a PID for target {self.config.target!r} via pgrep"
            )
        return pid

    def run(self) -> int:
        pid = self.resolve_pid()
        self._open_csv()
        try:
            self._sample_loop(pid)
        finally:
            self._close_csv()
        return self._final_report()

    # internals ----------------------------------------------------------

    def _open_csv(self) -> None:
        if self.config.out_csv is None:
            return
        path = self.config.out_csv
        path.parent.mkdir(parents=True, exist_ok=True)
        # Append, not overwrite — repeated runs against the same path keep
        # historical samples lined up by timestamp column.
        self._csv_handle = path.open("a", encoding="utf-8", newline="")
        self._csv_writer = csv.writer(self._csv_handle)
        if path.stat().st_size == 0:
            self._csv_writer.writerow(["elapsed_secs", "wall_unix", "rss_kb"])
            self._csv_handle.flush()

    def _close_csv(self) -> None:
        if self._csv_handle is not None:
            try:
                self._csv_handle.flush()
            finally:
                self._csv_handle.close()
                self._csv_handle = None
                self._csv_writer = None

    def _sample_loop(self, pid: int) -> None:
        start = self._clock()
        deadline = start + self.config.duration_secs
        next_sample_at = start
        while not self._stop_requested:
            now = self._clock()
            if now >= deadline:
                break
            if now < next_sample_at:
                self._sleep(min(next_sample_at - now, 1.0))
                continue
            elapsed = now - start
            rss = self._rss_reader(pid)
            if rss is None:
                self._stdout.write(
                    f"[soak] PID {pid} disappeared at t={elapsed:.1f}s; aborting.\n"
                )
                self._stdout.flush()
                self._stop_requested = True
                break
            sample = Sample(elapsed_secs=elapsed, rss_kb=rss)
            self.samples.append(sample)
            self._stdout.write(
                f"[soak] t={elapsed:7.1f}s  rss={rss:>10d} KB  "
                f"(samples={len(self.samples)})\n"
            )
            self._stdout.flush()
            if self._csv_writer is not None and self._csv_handle is not None:
                self._csv_writer.writerow(
                    [f"{elapsed:.3f}", f"{time.time():.3f}", rss]
                )
                self._csv_handle.flush()
            next_sample_at += self.config.sample_every_secs

    def _final_report(self) -> int:
        drift = compute_drift_pct(self.samples, self.config.smooth_window_secs)
        if not self.samples:
            self._stdout.write("[soak] no samples collected; cannot judge drift.\n")
            return 1
        head_kb = self.samples[0].rss_kb
        tail_kb = self.samples[-1].rss_kb
        peak_kb = max(s.rss_kb for s in self.samples)
        self._stdout.write("\n[soak] --- final report ---\n")
        self._stdout.write(f"[soak] samples              = {len(self.samples)}\n")
        self._stdout.write(
            f"[soak] elapsed              = {self.samples[-1].elapsed_secs:.1f}s\n"
        )
        self._stdout.write(f"[soak] head rss             = {head_kb:>10d} KB\n")
        self._stdout.write(f"[soak] tail rss             = {tail_kb:>10d} KB\n")
        self._stdout.write(f"[soak] peak rss             = {peak_kb:>10d} KB\n")
        if drift is None:
            self._stdout.write("[soak] drift                = (insufficient data)\n")
        else:
            self._stdout.write(f"[soak] drift                = {drift:+.2f}%\n")
        self._stdout.write(
            f"[soak] max-drift threshold  = {self.config.max_drift_pct:.2f}%\n"
        )
        exit_code = decide_exit(drift, self.config.max_drift_pct)
        verdict = "PASS" if exit_code == 0 else "FAIL"
        self._stdout.write(f"[soak] verdict              = {verdict}\n")
        self._stdout.flush()
        return exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_72h_paper",
        description=(
            "Soak orchestrator for sniper-executor (Paper Operator Release "
            "gate #7). Samples RSS over a long run and asserts drift < threshold."
        ),
    )
    target_group = p.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--pid",
        type=int,
        help="explicit PID to monitor (skip pgrep auto-resolve)",
    )
    target_group.add_argument(
        "--target",
        help="binary name to resolve via pgrep -f -o (e.g. sniper-executor)",
    )
    p.add_argument(
        "--hours",
        type=float,
        default=72.0,
        help="how long to run the soak; fractional hours allowed (default 72)",
    )
    p.add_argument(
        "--sample-every-secs",
        type=float,
        default=60.0,
        help="time between RSS samples (default 60s)",
    )
    p.add_argument(
        "--smooth-window-secs",
        type=float,
        default=DEFAULT_SMOOTH_WINDOW_SECS,
        help=(
            "head/tail smoothing window for drift calculation (default 300s; "
            "if the run is shorter than 2x the window the script falls back "
            "to first-half-vs-second-half)"
        ),
    )
    p.add_argument(
        "--max-drift-pct",
        type=float,
        default=5.0,
        help="positive drift above this percent fails the run (default 5.0)",
    )
    p.add_argument(
        "--out-csv",
        type=Path,
        default=None,
        help="optional path to write per-sample CSV (created/appended)",
    )
    return p


def parse_args(argv: Sequence[str]) -> OrchestratorConfig:
    ns = build_parser().parse_args(argv)
    duration_secs = float(ns.hours) * 3600.0
    if duration_secs <= 0:
        raise SystemExit("--hours must be > 0")
    if ns.sample_every_secs <= 0:
        raise SystemExit("--sample-every-secs must be > 0")
    if ns.smooth_window_secs <= 0:
        raise SystemExit("--smooth-window-secs must be > 0")
    return OrchestratorConfig(
        pid=ns.pid,
        target=ns.target,
        duration_secs=duration_secs,
        sample_every_secs=ns.sample_every_secs,
        smooth_window_secs=ns.smooth_window_secs,
        max_drift_pct=ns.max_drift_pct,
        out_csv=ns.out_csv,
    )


def install_signal_handlers(orch: Orchestrator) -> None:
    def _handler(signum, _frame) -> None:
        sys.stderr.write(f"\n[soak] received signal {signum}; stopping cleanly...\n")
        sys.stderr.flush()
        orch.request_stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread, or signal not supported (e.g. inside a
            # pytest runner). Sample loop honours self._stop_requested anyway.
            pass


def main(argv: Optional[Iterable[str]] = None) -> int:
    config = parse_args(list(argv if argv is not None else sys.argv[1:]))
    orch = Orchestrator(config)
    install_signal_handlers(orch)
    try:
        return orch.run()
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, ValueError, OSError) as exc:
        sys.stderr.write(f"[soak] fatal: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
