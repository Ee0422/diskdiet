import os
import time

from conftest import write

from diskdiet.tidy import apply_tidy, category_for, plan_tidy, undo_last


def test_categories(tmp_path):
    assert category_for(tmp_path / "a.PDF") == "Documents"
    assert category_for(tmp_path / "x.tar.gz") == "Archives"
    assert category_for(tmp_path / "setup.exe") == "Installers"
    assert category_for(tmp_path / "weird.xyz") == "Other"
    assert category_for(tmp_path / "movie.mp4.crdownload") is None
    assert category_for(tmp_path / ".DS_Store") is None


def test_tidy_and_undo(tmp_path):
    dl = tmp_path / "Downloads"
    write(dl / "cat.jpg")
    write(dl / "report.pdf")
    write(dl / "setup.exe")
    write(dl / "half.zip.part")
    write(dl / "my folder" / "keep.txt")
    write(dl / "Images" / "cat.jpg", "already here")

    plan = plan_tidy(dl)
    targets = {m.src.name: m.dst.relative_to(dl).as_posix() for m in plan.moves}
    assert targets == {
        "cat.jpg": "Images/cat (1).jpg",  # never overwrites
        "report.pdf": "Documents/report.pdf",
        "setup.exe": "Installers/setup.exe",
    }

    _, done = apply_tidy(plan)
    assert len(done) == 3
    assert (dl / "Documents" / "report.pdf").exists()
    assert not (dl / "report.pdf").exists()
    assert (dl / "half.zip.part").exists()
    assert (dl / "my folder" / "keep.txt").exists()

    result = undo_last()
    assert result.restored == 3
    assert (dl / "report.pdf").exists()
    assert (dl / "cat.jpg").exists()
    assert not (dl / "Documents").exists()  # empty folders we made are removed
    assert (dl / "Images" / "cat.jpg").read_text() == "already here"
    # second undo has nothing left to do
    assert undo_last().journal is None


def test_older_than(tmp_path):
    dl = tmp_path / "dl"
    old = write(dl / "old.pdf")
    write(dl / "new.pdf")
    past = time.time() - 10 * 86400
    os.utime(old, (past, past))
    plan = plan_tidy(dl, older_than_days=7)
    assert [m.src.name for m in plan.moves] == ["old.pdf"]
