#[test]
fn cli_emits_json() {
    let words_path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../generator/output/words.json");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_crossword_phase1"))
        .args([
            "--size",
            "7",
            "--words",
            words_path.to_str().expect("words path"),
            "--seed",
            "42",
            "--preparation-attempts",
            "1",
        ])
        .output()
        .expect("run cli");
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: serde_json::Value = serde_json::from_slice(&output.stdout).expect("json");
    assert!(json.get("template").is_some());
    assert!(json.get("quality").is_some());
}
