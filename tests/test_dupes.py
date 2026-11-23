import os
import time

from conftest import write

from diskdiet.dupes import find_big_files, find_duplicates


def test_finds_duplicates(tmp_path):
    data = os.urandom(200_000)
    a = write(tmp_path / "a" / "photo.jpg", data)
    b = write(tmp_path / "b" / "photo copy.jpg", data)
    write(tmp_path / "c" / "other.jpg", os.urandom(200_000))  # same size, different
    # Same first 64 KB, different ending: must not be reported.
    write(tmp_path / "d" / "tricky.bin", data[:-1] + bytes([data[-1] ^ 1]))
    past = time.time() - 1000
    os.utime(a, (past, past))

    groups = find_duplicates(tmp_path, min_size=1)
    assert len(groups) == 1
    g = groups[0]
    assert set(g.paths) == {a, b}
    assert g.wasted == len(data)
    assert g.keeper() == a  # oldest copy is kept


def test_small_files_ignored(tmp_path):
    write(tmp_path / "x.txt", "same")
    write(tmp_path / "y.txt", "same")
    assert find_duplicates(tmp_path) == []  # default min size is 1 MB
    assert len(find_duplicates(tmp_path, min_size=1)) == 1


def test_hard_links_are_not_duplicates(tmp_path):
    a = write(tmp_path / "a.bin", b"z" * 5000)
    try:
        os.link(a, tmp_path / "b.bin")
    except (OSError, NotImplementedError):
        return
    assert find_duplicates(tmp_path, min_size=1) == []


def test_big_files(tmp_path):
    write(tmp_path / "small", b"x" * 10)
    write(tmp_path / "huge", b"x" * 5000)
    write(tmp_path / "sub" / "big", b"x" * 3000)
    found = find_big_files(tmp_path, min_size=1000)
    assert [f.path.name for f in found] == ["huge", "big"]
