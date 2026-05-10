"""Unit tests for the soak orchestrator.

Exercise every pure function (drift / decide_exit / sample windowing) and
the orchestration loop with a faked clock + faked RSS reader so the tests
finish in milliseconds and never touch a real process.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

# Allow running both as `pytest tools/soak/` and from the package directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_72h_paper as soak  # noqa: E402  (path-mangled import on purpose)


# ---------------------------------------------------------------------------
# compute_drift_pct / decide_exit
# ---------------------------------------------------------------------------

def _series(values: list[int], step: float = 60.0) -> list[soak.Sample]:
    return [soak.Sample(elapsed_secs=i * step, rss_kb=v) for i, v in enumerate(values)]


def test_drift_returns_none_for_too_few_samples():
    assert soak.compute_drift_pct([]) is None
    assert soak.compute_drift_pct(_series([100])) is None


def test_drift_short_run_uses_first_vs_second_half():
    # 10 samples at 60s steps => total elapsed 540s, well below 2*300s window.
    # Falls back to first-half / second-half mean.
    samples = _series([100, 100, 100, 100, 100, 110, 110, 110, 110, 110])
    drift = soak.compute_drift_pct(samples, smooth_window_secs=300.0)
    assert drift is not None
    assert drift == pytest.approx(10.0, abs=0.01)


def test_drift_long_run_uses_head_and_tail_window():
    # 12 samples at 60s steps over 660s total. With smooth_window=120s the
    # head window is the first 3 samples (t=0,60,120) and the tail window is
    # the last 3 samples (t=540,600,660).
    head_vals = [100, 100, 100]
    middle = [105, 110, 115, 120, 115, 110]
    tail_vals = [105, 105, 105]
    samples = _series(head_vals + middle + tail_vals)
    drift = soak.compute_drift_pct(samples, smooth_window_secs=120.0)
    assert drift is not None
    # head_avg=100, tail_avg=105 → +5%
    assert drift == pytest.approx(5.0, abs=0.01)


def test_drift_handles_negative_drift():
    samples = _series([200, 200, 200, 200, 200, 180, 180, 180, 180, 180])
    drift = soak.compute_drift_pct(samples, smooth_window_secs=300.0)
    assert drift is not None
    assert drift == pytest.approx(-10.0, abs=0.01)


def test_decide_exit_codes():
    assert soak.decide_exit(0.5, 5.0) == 0
    assert soak.decide_exit(5.0, 5.0) == 0  # equal → pass
    assert soak.decide_exit(5.01, 5.0) == 1
    assert soak.decide_exit(-7.0, 5.0) == 0  # negative drift never fails
    assert soak.decide_exit(None, 5.0) == 1  # missing data is conservative


# ---------------------------------------------------------------------------
# read_rss_kb
# ---------------------------------------------------------------------------

def test_read_rss_kb_parses_proc_status(tmp_path, monkeypatch):
    fake_proc = tmp_path / "proc"
    pid_dir = fake_proc / "12345"
    pid_dir.mkdir(parents=True)
    (pid_dir / "status").write_text(
        "Name:\tsniper-executor\nState:\tS (sleeping)\nVmRSS:\t  98765 kB\n"
    )
    # Patch the path lookup to point into our fake /proc.
    real_path_cls = soak.Path

    def fake_path(arg, *args, **kwargs):
        if isinstance(arg, str) and arg.startswith("/proc/"):
            return real_path_cls(str(fake_proc) + arg[len("/proc"):])
        return real_path_cls(arg, *args, **kwargs)

    monkeypatch.setattr(soak, "Path", fake_path)
    assert soak.read_rss_kb(12345) == 98765


def test_read_rss_kb_returns_none_when_pid_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(soak, "Path", lambda *_a, **_k: tmp_path / "definitely-not-here")
    monkeypatch.setattr(soak.shutil, "which", lambda _: None)
    assert soak.read_rss_kb(99999) is None


# ---------------------------------------------------------------------------
# Orchestrator (full loop with faked clock and rss reader)
# ---------------------------------------------------------------------------

class _FakeClock:
    """Monotonic clock + sleep replacement that advances synthetically."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, secs: float) -> None:
        self.now += secs


def test_orchestrator_runs_with_explicit_pid_and_writes_csv(tmp_path):
    clock = _FakeClock()
    samples_to_serve = iter([100_000, 100_500, 101_000, 101_500, 102_000])

    def rss_reader(pid: int) -> int:
        assert pid == 4242
        return next(samples_to_serve)

    csv_path = tmp_path / "soak.csv"
    cfg = soak.OrchestratorConfig(
        pid=4242,
        target=None,
        duration_secs=240.0,
        sample_every_secs=60.0,
        smooth_window_secs=120.0,
        max_drift_pct=5.0,
        out_csv=csv_path,
    )
    out = io.StringIO()
    orch = soak.Orchestrator(
        cfg, clock=clock, sleep=clock.sleep, rss_reader=rss_reader, stdout=out
    )
    exit_code = orch.run()
    assert exit_code == 0  # +2% drift ≤ 5% threshold
    assert len(orch.samples) >= 4
    assert csv_path.exists()
    csv_lines = csv_path.read_text().splitlines()
    assert csv_lines[0] == "elapsed_secs,wall_unix,rss_kb"
    # data rows
    assert len(csv_lines) >= 5
    assert "PASS" in out.getvalue()


def test_orchestrator_fails_when_drift_exceeds_threshold(tmp_path):
    clock = _FakeClock()
    # +20% drift, 8 samples
    samples = iter([100_000, 100_000, 100_000, 105_000, 110_000, 115_000, 120_000, 120_000])

    cfg = soak.OrchestratorConfig(
        pid=1,
        target=None,
        duration_secs=480.0,
        sample_every_secs=60.0,
        smooth_window_secs=120.0,
        max_drift_pct=5.0,
        out_csv=None,
    )
    out = io.StringIO()
    orch = soak.Orchestrator(
        cfg, clock=clock, sleep=clock.sleep, rss_reader=lambda _pid: next(samples), stdout=out
    )
    exit_code = orch.run()
    assert exit_code == 1
    assert "FAIL" in out.getvalue()


def test_orchestrator_aborts_when_pid_disappears():
    clock = _FakeClock()
    rss_values = iter([100_000, 100_500, None, 100_000])

    cfg = soak.OrchestratorConfig(
        pid=1,
        target=None,
        duration_secs=600.0,
        sample_every_secs=60.0,
        smooth_window_secs=120.0,
        max_drift_pct=5.0,
        out_csv=None,
    )
    out = io.StringIO()
    orch = soak.Orchestrator(
        cfg, clock=clock, sleep=clock.sleep, rss_reader=lambda _pid: next(rss_values), stdout=out
    )
    exit_code = orch.run()
    # Aborted with only 2 samples but we still attempt to grade them.
    assert "PID 1 disappeared" in out.getvalue()
    assert exit_code in (0, 1)  # depends on the small sample drift; the abort message is the test


def test_orchestrator_resolve_pid_requires_one_of_pid_or_target():
    cfg = soak.OrchestratorConfig(
        pid=None,
        target=None,
        duration_secs=60.0,
        sample_every_secs=10.0,
        smooth_window_secs=30.0,
        max_drift_pct=5.0,
        out_csv=None,
    )
    orch = soak.Orchestrator(cfg)
    with pytest.raises(ValueError):
        orch.resolve_pid()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_parse_args_defaults():
    cfg = soak.parse_args(["--pid", "1234"])
    assert cfg.pid == 1234
    assert cfg.target is None
    assert cfg.duration_secs == pytest.approx(72 * 3600.0)
    assert cfg.sample_every_secs == 60.0
    assert cfg.max_drift_pct == 5.0


def test_parse_args_target():
    cfg = soak.parse_args(["--target", "sniper-executor", "--hours", "0.05"])
    assert cfg.pid is None
    assert cfg.target == "sniper-executor"
    assert cfg.duration_secs == pytest.approx(180.0)


def test_parse_args_rejects_zero_hours():
    with pytest.raises(SystemExit):
        soak.parse_args(["--pid", "1", "--hours", "0"])


def test_parse_args_requires_pid_or_target():
    with pytest.raises(SystemExit):
        soak.parse_args(["--hours", "1"])
