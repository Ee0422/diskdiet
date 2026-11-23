import json

from conftest import write

from diskdiet.cli import main


def test_scan_json(tmp_path, capsys):
    write(tmp_path / "p" / "package.json", "{}")
    write(tmp_path / "p" / "node_modules" / "a.js", b"x" * 100)
    assert main(["scan", str(tmp_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["kind"] == "Node.js packages"
    assert out[0]["bytes"] > 0


def test_clean_dry_run_keeps_files(tmp_path):
    write(tmp_path / "p" / "package.json", "{}")
    nm = write(tmp_path / "p" / "node_modules" / "a.js", b"x" * 100)
    assert main(["clean", str(tmp_path), "--yes", "--dry-run"]) == 0
    assert nm.exists()


def test_tidy_cli_roundtrip(tmp_path):
    dl = tmp_path / "dl"
    write(dl / "a.pdf")
    assert main(["tidy", str(dl), "--yes"]) == 0
    assert (dl / "Documents" / "a.pdf").exists()
    assert main(["undo"]) == 0
    assert (dl / "a.pdf").exists()


def test_bad_size_is_friendly(tmp_path):
    assert main(["big", str(tmp_path), "--min-size", "huge"]) == 2
