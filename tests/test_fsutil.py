import pytest

from diskdiet.fsutil import human_size, parse_selection, parse_size, unique_path


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024**3) == "5.0 GB"


@pytest.mark.parametrize(
    "text,expected",
    [("2048", 2048), ("1KB", 1024), ("500MB", 500 * 1024**2), ("1.5g", int(1.5 * 1024**3)),
     ("2 GiB", 2 * 1024**3)],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_parse_size_rejects_garbage():
    with pytest.raises(ValueError):
        parse_size("lots")


def test_parse_selection():
    assert parse_selection("all", 3) == [0, 1, 2]
    assert parse_selection("none", 3) == []
    assert parse_selection("1, 3", 3) == [0, 2]
    assert parse_selection("2-4 1", 5) == [0, 1, 2, 3]
    with pytest.raises(ValueError):
        parse_selection("9", 3)
    with pytest.raises(ValueError):
        parse_selection("abc", 3)


def test_unique_path(tmp_path):
    a = tmp_path / "a.txt"
    assert unique_path(a) == a
    a.write_text("1")
    assert unique_path(a) == tmp_path / "a (1).txt"
