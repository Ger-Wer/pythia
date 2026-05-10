//! End-to-end determinism check for the `replay` binary (gate #9).
//!
//! Builds the binary via `CARGO_BIN_EXE_replay`, runs it twice against the
//! committed golden fixtures, and asserts:
//!
//! 1. Both runs produce byte-identical stdout.
//! 2. The first run matches the committed `expected.jsonl` golden output.
//!
//! If the matcher behaviour changes intentionally (e.g. additional output
//! fields, hash domain bump), regenerate the golden via:
//!   cargo build -p sniper-ingest --bin replay --release
//!   ./target/release/replay \\
//!     --dump services/sniper-ingest/tests/fixtures/replay/dump.jsonl \\
//!     --watchlist services/sniper-ingest/tests/fixtures/replay/watchlist.jsonl \\
//!     --clock-mode frozen --deterministic-ids \\
//!     > services/sniper-ingest/tests/fixtures/replay/expected.jsonl

use std::path::PathBuf;
use std::process::{Command, Stdio};

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/replay")
}

fn run_replay() -> Vec<u8> {
    let bin = env!("CARGO_BIN_EXE_replay");
    let fixtures = fixtures_dir();
    let output = Command::new(bin)
        .arg("--dump")
        .arg(fixtures.join("dump.jsonl"))
        .arg("--watchlist")
        .arg(fixtures.join("watchlist.jsonl"))
        .arg("--clock-mode")
        .arg("frozen")
        .arg("--deterministic-ids")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .expect("failed to spawn replay binary");
    assert!(
        output.status.success(),
        "replay exited non-zero: {}\nstderr:\n{}",
        output.status,
        String::from_utf8_lossy(&output.stderr),
    );
    output.stdout
}

#[test]
fn replay_output_is_byte_identical_across_runs() {
    let a = run_replay();
    let b = run_replay();
    assert_eq!(
        a,
        b,
        "replay stdout differs between runs (gate #9 violation)\n\
         run A ({} bytes):\n{}\n\
         run B ({} bytes):\n{}",
        a.len(),
        String::from_utf8_lossy(&a),
        b.len(),
        String::from_utf8_lossy(&b),
    );
}

#[test]
fn replay_output_matches_golden_expected() {
    let actual = run_replay();
    let expected_path = fixtures_dir().join("expected.jsonl");
    let expected = std::fs::read(&expected_path)
        .unwrap_or_else(|e| panic!("read {}: {e}", expected_path.display()));
    if actual != expected {
        panic!(
            "replay stdout drifted from golden ({})\n\
             ACTUAL ({} bytes):\n{}\n\
             EXPECTED ({} bytes):\n{}\n\n\
             If this is intentional, regenerate:\n\
               cargo build -p sniper-ingest --bin replay --release\n\
               ./target/release/replay \\\n\
                 --dump {dump} \\\n\
                 --watchlist {wl} \\\n\
                 --clock-mode frozen --deterministic-ids \\\n\
                 > {expected}",
            expected_path.display(),
            actual.len(),
            String::from_utf8_lossy(&actual),
            expected.len(),
            String::from_utf8_lossy(&expected),
            dump = fixtures_dir().join("dump.jsonl").display(),
            wl = fixtures_dir().join("watchlist.jsonl").display(),
            expected = expected_path.display(),
        );
    }
}
