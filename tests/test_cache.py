from conftest import write

from diskdiet import cache
from diskdiet.junk import ScanProgress, scan_junk


def _project(tmp_path):
    write(tmp_path / "p" / "package.json", "{}")
    write(tmp_path / "p" / "node_modules" / "a.js", b"x" * 100)


def test_roundtrip_and_staleness(tmp_path, monkeypatch):
    _project(tmp_path)
    items = scan_junk(tmp_path)
    cache.save_scan(tmp_path, 0, items)

    loaded, age = cache.load_scan(tmp_path, 0)
    assert [i.path for i in loaded] == [i.path for i in items]
    assert age < 5

    assert cache.load_scan(tmp_path / "p", 0) is None  # different folder
    assert cache.load_scan(tmp_path, 0, max_age=-1) is None  # too old


def test_drops_folders_that_are_gone(tmp_path):
    _project(tmp_path)
    cache.save_scan(tmp_path, 0, scan_junk(tmp_path))
    (tmp_path / "p" / "node_modules" / "a.js").unlink()
    (tmp_path / "p" / "node_modules").rmdir()
    loaded, _ = cache.load_scan(tmp_path, 0)
    assert loaded == []


def test_older_than_reuse_rules(tmp_path):
    _project(tmp_path)
    cache.save_scan(tmp_path, 0, scan_junk(tmp_path))
    # A broad scan can answer a narrower question (the fresh project is filtered out)...
    loaded, _ = cache.load_scan(tmp_path, 30)
    assert loaded == []
    # ...but a narrow scan cannot answer a broader one.
    cache.save_scan(tmp_path, 30, [])
    assert cache.load_scan(tmp_path, 0) is None


def test_progress_callbacks(tmp_path):
    _project(tmp_path)
    write(tmp_path / "q" / "Cargo.toml", "")
    write(tmp_path / "q" / "target" / "x", b"x" * 10)

    class Recorder(ScanProgress):
        def __init__(self):
            self.searched = 0
            self.measured = []

        def searching(self, folder, folders_checked, found):
            self.searched = folders_checked

        def measuring(self, done, total, folder, bytes_so_far):
            self.measured.append((done, total))

    rec = Recorder()
    scan_junk(tmp_path, progress=rec)
    assert rec.searched >= 3
    assert rec.measured == [(1, 2), (2, 2)]
