#[test]
fn cli_emits_json() {
    let words_path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/words.json");
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
    assert!(json.get("words").is_some());
    assert!(
        json["words"]
            .as_array()
            .unwrap_or(&Vec::new())
            .iter()
            .all(|word| word.get("original").and_then(|value| value.as_str()).is_some())
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("inward grid"), "stderr={stderr}");
    assert!(stderr.contains("outward grid"), "stderr={stderr}");
    assert!(stderr.contains('+'), "stderr={stderr}");
}

#[test]
fn cli_invalid_size_fails_cleanly() {
    let words_path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/words.json");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_crossword_phase1"))
        .args([
            "--size",
            "6",
            "--words",
            words_path.to_str().expect("words path"),
            "--seed",
            "42",
            "--preparation-attempts",
            "1",
        ])
        .output()
        .expect("run cli");
    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("unsupported size"),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn dictionary_profile_cli_writes_sidecar_json() {
    let words_path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/words.json");
    let tempdir = tempfile::tempdir().expect("tempdir");
    let output_path = tempdir.path().join("words.profile.json");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_crossword_dictionary_profile"))
        .args([
            "--words",
            words_path.to_str().expect("words path"),
            "--output",
            output_path.to_str().expect("profile path"),
        ])
        .output()
        .expect("run profile cli");
    assert!(
        output.status.success(),
        "stderr={}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&output_path).expect("read profile"))
            .expect("profile json");
    assert!(json["sizes"].get("7").is_some());
    assert!(json["sizes"]["7"]["positional"].is_object());
}
