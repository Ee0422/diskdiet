import os
import time

from conftest import write

from diskdiet.junk import match_rule, scan_junk


def test_finds_real_leftovers(tmp_path):
    web = tmp_path / "web"
    write(web / "package.json", "{}")
    write(web / "node_modules" / "left-pad" / "index.js", b"x" * 1000)
    write(web / "node_modules" / "left-pad" / "node_modules" / "deep.js", b"x" * 10)

    py = tmp_path / "py"
    write(py / ".venv" / "pyvenv.cfg", "home = /usr")
    write(py / ".venv" / "lib" / "big.so", b"x" * 5000)

    rust = tmp_path / "rs"
    write(rust / "Cargo.toml", "")
    write(rust / "target" / "debug" / "app", b"x" * 3000)

    found = scan_junk(tmp_path)
    kinds = {(f.path.relative_to(tmp_path).as_posix(), f.kind) for f in found}
    assert ("web/node_modules", "Node.js packages") in kinds
    assert ("py/.venv", "Python virtualenv") in kinds
    assert ("rs/target", "Rust build") in kinds
    # nested node_modules is inside the outer one and must not be listed twice
    assert len(found) == 3
    # biggest first
    assert found[0].path.name == ".venv"


def test_needs_evidence(tmp_path):
    # A folder called "target" or "build" with no project file is left alone.
    write(tmp_path / "photos" / "target" / "shot.jpg", b"x" * 100)
    write(tmp_path / "school" / "build" / "essay.docx", b"x" * 100)
    write(tmp_path / "notes" / "venv" / "todo.txt", b"x")
    assert scan_junk(tmp_path) == []
    assert match_rule(tmp_path / "photos" / "target") is None


def test_older_than_filters_active_projects(tmp_path):
    old = tmp_path / "old"
    write(old / "package.json", "{}")
    write(old / "node_modules" / "a.js", b"x" * 10)
    past = time.time() - 90 * 86400
    os.utime(old / "package.json", (past, past))

    new = tmp_path / "new"
    write(new / "package.json", "{}")
    write(new / "node_modules" / "a.js", b"x" * 10)

    found = scan_junk(tmp_path, min_age_days=30)
    assert [f.project.name for f in found] == ["old"]
    assert found[0].project_age > 80 * 86400


def test_skips_git_folders(tmp_path):
    write(tmp_path / "repo" / ".git" / "x" / "package.json", "{}")
    write(tmp_path / "repo" / ".git" / "x" / "node_modules" / "a.js", b"x")
    assert scan_junk(tmp_path) == []
