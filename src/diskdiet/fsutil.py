"""Small filesystem helpers shared by every command."""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from pathlib import Path

# Folders we never walk into: version control internals and OS-managed areas.
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash",
    ".Trashes",
}

# Skipped only directly inside the home folder: app data, not user projects.
HOME_SKIP_DIRS = {"Library", "AppData", ".cache", ".local", ".npm", ".cargo", ".rustup"}

# Virtual filesystems on Linux; never worth scanning.
SKIP_ABSOLUTE = {"/proc", "/sys", "/dev", "/run"}

_HOME = str(Path.home())


def should_skip_dir(entry: os.DirEntry, parent: str) -> bool:
    """True for folders no command should ever walk into."""
    if entry.name in SKIP_DIRS or entry.path in SKIP_ABSOLUTE:
        return True
    return entry.name in HOME_SKIP_DIRS and parent == _HOME

_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def data_dir() -> Path:
    """Where diskdiet keeps its small records (override with DISKDIET_HOME)."""
    base = os.environ.get("DISKDIET_HOME")
    return Path(base) if base else Path.home() / ".diskdiet"


def human_size(num_bytes: float) -> str:
    """Format a byte count like ``1.4 GB``."""
    size = float(num_bytes)
    for unit in _UNITS:
        if abs(size) < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"  # pragma: no cover


_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?)?\s*$", re.IGNORECASE)
_MULT = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}


def parse_size(text: str) -> int:
    """Parse ``500MB``, ``1.5g``, ``2048`` into a byte count."""
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"not a size: {text!r} (try 500MB or 2GB)")
    number, unit = match.groups()
    key = (unit or "").lower().rstrip("b").rstrip("i")
    return int(float(number) * _MULT[key])


def human_age(seconds: float) -> str:
    """Format an age like ``3 days`` or ``8 months``."""
    days = seconds / 86400
    if days < 1:
        return "today"
    if days < 60:
        n = int(days)
        return f"{n} day{'s' if n != 1 else ''}"
    if days < 730:
        n = int(days / 30)
        return f"{n} months"
    return f"{int(days / 365)} years"


def disk_usage(st: os.stat_result) -> int:
    """Space a file really takes on disk (what you get back by deleting it)."""
    blocks = getattr(st, "st_blocks", None)
    if blocks is not None:
        return blocks * 512
    return st.st_size  # Windows has no st_blocks


def dir_size(path: Path) -> tuple[int, int]:
    """Return ``(total_bytes, file_count)`` for a folder, without following symlinks."""
    total = 0
    count = 0
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += disk_usage(entry.stat(follow_symlinks=False))
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


def iter_files(root: Path, skip_hidden_dirs: bool = False) -> Iterator[os.DirEntry]:
    """Yield every regular file under ``root`` (symlinks are not followed)."""
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if should_skip_dir(entry, current):
                                continue
                            if skip_hidden_dirs and entry.name.startswith("."):
                                continue
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            yield entry
                    except OSError:
                        continue
        except OSError:
            continue


def age_seconds(mtime: float) -> float:
    return max(0.0, time.time() - mtime)


def unique_path(target: Path) -> Path:
    """Return ``target`` or ``name (1).ext``, ``name (2).ext``... if it exists."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        candidate = target.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def parse_selection(text: str, count: int) -> list[int]:
    """Turn ``1,3,5-7`` / ``all`` / ``none`` into zero-based indexes.

    Raises ``ValueError`` for anything out of range or malformed.
    """
    text = text.strip().lower()
    if text in {"", "n", "no", "none", "q", "quit"}:
        return []
    if text in {"a", "all", "y", "yes"}:
        return list(range(count))
    picked: set[int] = set()
    for part in re.split(r"[,\s]+", text):
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            if start > end:
                start, end = end, start
            numbers = range(start, end + 1)
        else:
            numbers = range(int(part), int(part) + 1)
        for n in numbers:
            if not 1 <= n <= count:
                raise ValueError(f"{n} is not between 1 and {count}")
            picked.add(n - 1)
    return sorted(picked)
