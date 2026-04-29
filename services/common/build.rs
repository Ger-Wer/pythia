//! Build script: generate Rust types from `schemas/dual_loop/v1/*.proto`.
//!
//! Generated code lands in `$OUT_DIR/dual_loop.v1.rs` and is included via
//! `include!` from `src/proto.rs`. We bundle `protoc` through
//! `protoc-bin-vendored` so the build never depends on a system-wide
//! protobuf compiler.

use std::path::PathBuf;

fn main() -> std::io::Result<()> {
    // Point `prost-build` at the vendored protoc binary.
    let protoc =
        protoc_bin_vendored::protoc_bin_path().expect("vendored protoc binary should be available");
    std::env::set_var("PROTOC", &protoc);

    let schemas_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("schemas");

    let proto_files = [
        "dual_loop/v1/common.proto",
        "dual_loop/v1/watchlist.proto",
        "dual_loop/v1/signal.proto",
        "dual_loop/v1/trigger.proto",
        "dual_loop/v1/mock.proto",
        "dual_loop/v1/execution.proto",
        "dual_loop/v1/control.proto",
    ];

    for f in &proto_files {
        println!("cargo:rerun-if-changed={}", schemas_root.join(f).display());
    }
    println!("cargo:rerun-if-changed=build.rs");

    let proto_paths: Vec<PathBuf> = proto_files.iter().map(|f| schemas_root.join(f)).collect();

    // Emit into $OUT_DIR so that `cargo fmt` (which doesn't run build scripts)
    // never requires the generated files to be present on disk.
    let mut config = prost_build::Config::new();
    config.bytes(["."]);
    config.compile_protos(&proto_paths, &[&schemas_root])?;

    Ok(())
}
