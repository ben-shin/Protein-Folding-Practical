"""The Pages artifact must contain the app, never repository or class data."""
import hashlib
import json

from scripts import build_web


def test_pages_build_contains_only_allowlisted_runtime_files(tmp_path, monkeypatch):
    output = tmp_path / "site"
    monkeypatch.setattr(build_web, "OUT", output)
    build_web.build()
    expected = set(build_web.ASSETS) | {".nojekyll", "build-manifest.json"}
    expected.update(f"core/folding_practical/{name}" for name in build_web.CORE)
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert actual == expected
    assert not any(path.lower().endswith((".md", ".csv")) for path in actual)
    manifest = json.loads((output / "build-manifest.json").read_text())
    for name, expected_hash in manifest["core_sha256"].items():
        assert hashlib.sha256((output / "core" / "folding_practical" / name).read_bytes()).hexdigest() == expected_hash
