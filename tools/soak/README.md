# soak / run_72h_paper

Manual soak orchestrator for `sniper-executor` — closes Paper Operator
Release **gate #7** ("72 h paper run без leakage").

The script is run by hand on a staging server before a major release, samples
the executor's RSS over hours, and asserts that the drift (head-vs-tail
smoothed average) does not exceed `--max-drift-pct`. Approved as a
**manual workflow** in the 2026-05-05 decision log — not on cron.

## Why a separate workspace under `tools/`

Like `tools/load_harness/`, this directory lives under `tools/` and is **not
copied into any production Docker image** — `services/Dockerfile`, the root
`Dockerfile`, and `gateway-api/Dockerfile` only `COPY services/`, `src/`,
`python/`, `dashboard/`. Putting the orchestrator under `tools/` guarantees
by construction that monitoring code can never leak into a production image.

Stdlib only — no `psutil`, no third-party deps. The soak runs on bare
staging hosts without a virtualenv.

## Usage

### Quick local smoke (a few minutes)

```bash
# In one shell, start something to monitor:
sleep 600 &
echo $!  # capture the pid

# In another shell, run the orchestrator for 1 minute against that pid:
python3 tools/soak/run_72h_paper.py \
  --pid 12345 \
  --hours 0.017 \
  --sample-every-secs 5 \
  --smooth-window-secs 10 \
  --max-drift-pct 5.0 \
  --out-csv /tmp/soak_smoke.csv
```

You should see per-sample lines on stdout, a final report, and a CSV with
the same data.

### Real soak (staging)

Start `sniper-executor` (or have it already running via `make dev-up`),
then start the harness as a continuous load source so the executor is
under realistic memory pressure:

```bash
# Terminal 1: dev stack (or run sniper-executor manually).
make dev-up

# Terminal 2: continuous load.
cd tools && cargo run --release --bin load_harness -- \
  --rate-per-min 1000 \
  --duration-secs 259200 \
  --target-p95-ms 50.0 \
  --max-drop-rate 0.01 \
  --no-assert

# Terminal 3: the soak orchestrator.
python3 tools/soak/run_72h_paper.py \
  --target sniper-executor \
  --hours 72 \
  --sample-every-secs 60 \
  --max-drift-pct 5.0 \
  --out-csv /var/log/soak/$(date +%F).csv
```

Exit code 0 → release-ready w.r.t. gate #7. Exit code 1 → drift exceeded
the threshold or the executor disappeared during the run; investigate
before tagging a release.

### Attach vs. spawn

The orchestrator does not currently spawn `sniper-executor` itself — it
only attaches to a pid (explicit via `--pid` or auto-resolved via
`--target` and `pgrep -f -o`). Reasoning: the operator usually wants the
executor running under the same supervisor that production uses
(systemd / Docker / `make dev-up`), and we don't want the soak script
duplicating those config paths. Bring up the executor however you would
in production, then point the script at it.

## CLI

| flag                        | default | meaning                                                              |
| --------------------------- | ------- | -------------------------------------------------------------------- |
| `--pid PID`                 | —       | explicit PID to monitor (mutually exclusive with `--target`)         |
| `--target NAME`             | —       | pgrep-resolvable binary name (e.g. `sniper-executor`)                |
| `--hours N`                 | 72      | soak duration in fractional hours                                    |
| `--sample-every-secs N`     | 60      | RSS sampling interval                                                |
| `--smooth-window-secs N`    | 300     | head/tail window for drift smoothing                                 |
| `--max-drift-pct N`         | 5.0     | positive drift above this percent fails the run (exit 1)             |
| `--out-csv PATH`            | —       | optional per-sample CSV (created/appended; flushed every sample)     |

## Drift math

```
head_avg = mean(rss in samples where elapsed ∈ [0, smooth_window_secs])
tail_avg = mean(rss in samples where elapsed ∈ [last - smooth_window_secs, last])
drift    = (tail_avg - head_avg) / head_avg * 100
```

If the run is shorter than `2 × smooth_window_secs`, the script falls back
to comparing the first half's mean to the second half's mean — useful for
short local smoke runs.

A **negative drift** (tail < head) is always treated as healthy, even if
its magnitude exceeds `--max-drift-pct`. The executor shrinking its
working set is not a leak signal worth aborting a release for.

## Layout

| file                          | role                                                         |
| ----------------------------- | ------------------------------------------------------------ |
| `run_72h_paper.py`            | single-file script: RSS reader + orchestrator + drift + CLI  |
| `test_run_72h_paper.py`       | pytest unit tests (15 tests; pure functions + faked clock)   |

## CI

Covered by the existing **Slow-loop Python (pytest)** job in
`.github/workflows/dual-loop-ci.yml` once `tools/**` is included in the
test discovery scope (see workflow change shipped in the same PR).
The 72 h soak itself is **not** in CI — it is a manual pre-release ritual.
