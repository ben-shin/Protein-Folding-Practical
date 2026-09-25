"""Publish only application files and pin the complete runtime to one release."""
import hashlib
import json
import shutil

from scripts import build_web


def test_pages_build_contains_only_allowlisted_runtime_files(tmp_path, monkeypatch):
    output = tmp_path / "site"
    monkeypatch.setattr(build_web, "OUT", output)
    manifest = build_web.build()
    stable = set(build_web.ASSETS) | {".nojekyll", "build-manifest.json"}
    stable.update(f"core/folding_practical/{name}" for name in build_web.CORE)
    release = f"releases/{manifest['build_id']}"
    expected = stable | {f"{release}/{path}" for path in stable if path != ".nojekyll"}
    actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert actual == expected
    assert not any(path.lower().endswith((".md", ".csv")) for path in actual)
    assert json.loads((output / manifest["release_index"]).with_name("build-manifest.json").read_text()) == manifest
    for name, expected_hash in manifest["core_sha256"].items():
        for base in (output, output / release):
            assert hashlib.sha256((base / "core" / "folding_practical" / name).read_bytes()).hexdigest() == expected_hash
    index = (output / "index.html").read_text()
    pinned_index = (output / manifest["release_index"]).read_text()
    for asset in ("app.js", "styles.css", "favicon.svg"):
        assert f'"./{release}/{asset}"' in index
        assert f'"./{asset}"' in pinned_index
    assert f'name="application-build" content="{manifest["build_id"]}"' in index
    assert (output / release / "worker.js").read_bytes() == (build_web.ROOT / "web" / "worker.js").read_bytes()


def test_core_update_changes_every_runtime_entry_path(tmp_path, monkeypatch):
    source = tmp_path / "source"
    for folder, names in (("web", build_web.ASSETS), ("folding_practical", build_web.CORE)):
        (source / folder).mkdir(parents=True)
        for name in names:
            shutil.copyfile(build_web.ROOT / folder / name, source / folder / name)
    output = tmp_path / "site"
    monkeypatch.setattr(build_web, "ROOT", source)
    monkeypatch.setattr(build_web, "OUT", output)
    before = build_web.build()
    assert build_web.build()["build_id"] == before["build_id"]
    analysis = source / "folding_practical" / "analysis.py"
    analysis.write_bytes(analysis.read_bytes() + b"\n# A new example implementation\n")
    after = build_web.build()
    assert before["build_id"] != after["build_id"]
    assert before["release_index"] != after["release_index"]
    index = (output / "index.html").read_text()
    assert after["build_id"] in index
    assert before["build_id"] not in index
    assert after["core_sha256"]["analysis.py"] != before["core_sha256"]["analysis.py"]
