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


def test_clean_reuses_recent_scan(tmp_path, monkeypatch, capsys):
    write(tmp_path / "p" / "package.json", "{}")
    nm = write(tmp_path / "p" / "node_modules" / "a.js", b"x" * 100)
    assert main(["scan", str(tmp_path), "--json"]) == 0
    capsys.readouterr()

    calls = []
    import diskdiet.cli as cli

    real = cli.scan_junk
    monkeypatch.setattr(cli, "scan_junk", lambda *a, **k: calls.append(1) or real(*a, **k))

    assert main(["clean", str(tmp_path), "--yes", "--dry-run"]) == 0
    assert calls == []  # no second search
    assert "Using your scan from" in capsys.readouterr().out
    assert nm.exists()

    assert main(["clean", str(tmp_path), "--yes", "--dry-run", "--rescan"]) == 0
    assert calls == [1]


def test_dupes_shows_steps(tmp_path, capsys):
    write(tmp_path / "a.bin", b"q" * 200_000)
    write(tmp_path / "b.bin", b"q" * 200_000)
    assert main(["dupes", str(tmp_path), "--min-size", "1KB"]) == 0
    out = capsys.readouterr().out
    assert "Step 3 of 3" in out
    assert "1 set of duplicates" in out
