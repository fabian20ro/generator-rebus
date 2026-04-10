"""Build dictionary scarcity/profile artifact for Rust phase-1."""

from __future__ import annotations

from pathlib import Path

from rebus_generator.platform.io.rust_bridge import rebuild_dictionary_profile


def run(input_file: str, output_file: str, **_kwargs) -> None:
    words_path = Path(input_file)
    if output_file != "-":
        raise ValueError("profile phase writes to the default sidecar artifact only")
    rebuild_dictionary_profile(words_path, download_if_missing=True)
