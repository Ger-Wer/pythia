//! Build script: generate Rust types from `schemas/dual_loop/v1/*.proto`.
//!
//! Generated code lands in `src/generated/` (git-ignored) and is re-exported
//! via `dual_loop_common::proto::{common, watchlist, signal, trigger, execution, control}`.
//!
//! We bundle `protoc` through `protoc-bin-vendored` so the build never
//! depends on a system-wide protobuf compiler.

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
        "dual_loop/v1/execution.proto",
        "dual_loop/v1/control.proto",
    ];

    for f in &proto_files {
        println!("cargo:rerun-if-changed={}", schemas_root.join(f).display());
    }
    println!("cargo:rerun-if-changed=build.rs");

    let out_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("src")
        .join("generated");
    std::fs::create_dir_all(&out_dir)?;

    let proto_paths: Vec<PathBuf> = proto_files.iter().map(|f| schemas_root.join(f)).collect();

    let mut config = prost_build::Config::new();
    config.out_dir(&out_dir);
    config.bytes(["."]);
    config.compile_protos(&proto_paths, &[&schemas_root])?;

    // prost emits a single file per proto package: `dual_loop.v1.rs`.
    // Write a tiny `mod.rs` that re-exports it under logical module names.
    let mod_rs = out_dir.join("mod.rs");
    std::fs::write(
        &mod_rs,
        "// @generated — do not edit. See build.rs.\n\
         #[allow(clippy::all, dead_code, missing_docs)]\n\
         pub mod dual_loop_v1 {\n    \
             include!(\"dual_loop.v1.rs\");\n\
         }\n",
    )?;

    Ok(())
}
