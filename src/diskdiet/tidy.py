"""Sort a messy folder (usually Downloads) into category subfolders, with undo.

Every run writes a small journal of exactly which file moved where, so
``diskdiet undo`` can put everything back.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import data_dir, unique_path

CATEGORIES: dict[str, set[str]] = {
    "Images": {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".tiff",
        ".tif", ".svg", ".ico", ".raw", ".cr2", ".nef", ".avif",
    },
    "Videos": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma"},
    "Documents": {
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".pages", ".epub",
        ".xls", ".xlsx", ".ods", ".csv", ".numbers", ".ppt", ".pptx", ".odp", ".key",
    },
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"},
    "Installers": {
        ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".apk", ".msix",
        ".iso",
    },
    "Code": {
        ".py", ".js", ".ts", ".json", ".html", ".css", ".java", ".c", ".cpp", ".h",
        ".go", ".rs", ".rb", ".php", ".sh", ".ipynb", ".sql", ".yaml", ".yml", ".xml",
    },
    "Fonts": {".ttf", ".otf", ".woff", ".woff2"},
    "Torrents": {".torrent"},
}

# Files that are still downloading. Moving them would break the download.
IN_PROGRESS = {".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload"}

OTHER = "Other"

_EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts}


def category_for(path: Path) -> str | None:
    """Category folder for a file, or ``None`` if we should leave it alone."""
    name = path.name
    if name.startswith(".") or name.lower() in {"desktop.ini", "thumbs.db"}:
        return None
    suffix = path.suffix.lower()
    if suffix in IN_PROGRESS:
        return None
    # .tar.gz and friends
    if name.lower().endswith((".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst")):
        return "Archives"
    return _EXT_TO_CATEGORY.get(suffix, OTHER)


@dataclass
class Move:
    src: Path
    dst: Path


@dataclass
class TidyPlan:
    root: Path
    moves: list[Move] = field(default_factory=list)

    def by_category(self) -> dict[str, list[Move]]:
        out: dict[str, list[Move]] = {}
        for m in self.moves:
            out.setdefault(m.dst.parent.name, []).append(m)
        return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


def plan_tidy(root: Path, older_than_days: float = 0) -> TidyPlan:
    """Work out where each loose file in ``root`` should go. Moves nothing."""
    plan = TidyPlan(root)
    cutoff = time.time() - older_than_days * 86400
    category_names = set(CATEGORIES) | {OTHER}
    taken: set[Path] = set()
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name.lower())
    except OSError:
        return plan
    for entry in entries:
        try:
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                continue
        except OSError:
            continue
        src = Path(entry.path)
        cat = category_for(src)
        if cat is None or src.name in category_names:
            continue
        dst = unique_path(root / cat / src.name)
        # Two files planned for the same name in this run (e.g. a.PDF and a.pdf
        # on a case-sensitive disk): keep numbering until it is free.
        i = 1
        while dst in taken:
            dst = root / cat / f"{src.stem} ({i}){src.suffix}"
            dst = unique_path(dst)
            i += 1
        taken.add(dst)
        plan.moves.append(Move(src, dst))
    return plan


# --------------------------------------------------------------------- journal


def history_dir() -> Path:
    return data_dir() / "history"


def apply_tidy(plan: TidyPlan) -> tuple[Path, list[Move]]:
    """Carry out ``plan`` and write a journal. Returns ``(journal, done_moves)``."""
    done: list[Move] = []
    made_dirs: list[str] = []
    for move in plan.moves:
        folder = move.dst.parent
        if not folder.exists():
            folder.mkdir(parents=True)
            made_dirs.append(str(folder))
        if move.dst.exists() or not move.src.exists():
            continue
        try:
            shutil.move(str(move.src), str(move.dst))
        except OSError:
            continue
        done.append(move)

    hist = history_dir()
    hist.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    journal = unique_path(hist / f"tidy-{stamp}.json")
    journal.write_text(
        json.dumps(
            {
                "type": "tidy",
                "created": time.time(),
                "root": str(plan.root),
                "moves": [[str(m.src), str(m.dst)] for m in done],
                "made_dirs": made_dirs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return journal, done


def last_journal() -> Path | None:
    hist = history_dir()
    if not hist.exists():
        return None
    newest: tuple[float, Path] | None = None
    for j in hist.glob("tidy-*.json"):
        if j.name.endswith(".undone.json"):
            continue
        try:
            created = float(json.loads(j.read_text(encoding="utf-8")).get("created", 0))
        except (OSError, ValueError):
            continue
        if newest is None or created > newest[0]:
            newest = (created, j)
    return newest[1] if newest else None


@dataclass
class UndoResult:
    restored: int = 0
    skipped: list[str] = field(default_factory=list)
    journal: Path | None = None


def undo_last() -> UndoResult:
    """Move every file from the most recent tidy back where it was."""
    result = UndoResult()
    journal = last_journal()
    if journal is None:
        return result
    result.journal = journal
    data = json.loads(journal.read_text(encoding="utf-8"))
    for src_s, dst_s in reversed(data.get("moves", [])):
        src, dst = Path(src_s), Path(dst_s)
        if not dst.exists():
            result.skipped.append(f"{dst} is gone (moved or deleted since)")
            continue
        if src.exists():
            result.skipped.append(f"{src} already exists, left {dst.name} where it is")
            continue
        shutil.move(str(dst), str(src))
        result.restored += 1
    for d in reversed(data.get("made_dirs", [])):
        try:
            Path(d).rmdir()  # only succeeds if empty
        except OSError:
            pass
    journal.rename(journal.with_name(journal.stem + ".undone.json"))
    return result
