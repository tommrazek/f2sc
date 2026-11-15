//! Stamp the cadmpeg commit the decoder is built from, so the converter can tell a stale build.
fn main() {
    for p in ["../../cadmpeg/.git/HEAD", "../../cadmpeg/.git/refs/heads", "../../cadmpeg/.git/packed-refs"] {
        println!("cargo:rerun-if-changed={p}");
    }
    let head = std::process::Command::new("git")
        .args(["-C", "../../cadmpeg", "rev-parse", "HEAD"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_else(|| "unknown".into());
    println!("cargo:rustc-env=CADMPEG_COMMIT={head}");
}
