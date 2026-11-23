"""Find duplicate files and very large files.

Duplicates are found in three cheap-to-expensive passes so we rarely read
whole files: group by size, then by a hash of the first 64 KB, then by a
full hash. Only files that survive every pass are reported.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fsutil import iter_files

_CHUNK = 1024 * 1024
_HEAD = 64 * 1024


def _hash(path: str, limit: int | None = None) -> str | None:
    h = hashlib.blake2b(digest_size=20)
    read = 0
    try:
        with open(path, "rb") as fh:
            while True:
                want = _CHUNK if limit is None else min(_CHUNK, limit - read)
                if want <= 0:
                    break
                chunk = fh.read(want)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
    except OSError:
        return None
    return h.hexdigest()


@dataclass
class DupeGroup:
    size: int  # size of one copy
    paths: list[Path] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        """Space freed by keeping one copy."""
        return self.size * (len(self.paths) - 1)

    def keeper(self) -> Path:
        """The copy we keep: the oldest one (most likely the original)."""
        def mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return float("inf")

        return min(self.paths, key=lambda p: (mtime(p), len(str(p)), str(p)))


def find_duplicates(
    root: Path,
    min_size: int = 1024 * 1024,
    on_progress: Callable[[str], None] | None = None,
) -> list[DupeGroup]:
    """Return groups of identical files under ``root``, most wasted space first."""
    by_size: dict[int, list[str]] = defaultdict(list)
    seen_inodes: set[tuple[int, int]] = set()
    for entry in iter_files(root):
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_size < min_size:
            continue
        # Hard links already share their data; they are not real duplicates.
        if st.st_ino:
            key = (st.st_dev, st.st_ino)
            if key in seen_inodes:
                continue
            seen_inodes.add(key)
        by_size[st.st_size].append(entry.path)

    groups: list[DupeGroup] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_head: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            if on_progress:
                on_progress(p)
            digest = _hash(p, _HEAD)
            if digest:
                by_head[digest].append(p)
        for head_paths in by_head.values():
            if len(head_paths) < 2:
                continue
            if size <= _HEAD:
                # The head hash already covered the whole file.
                groups.append(DupeGroup(size, sorted(Path(p) for p in head_paths)))
                continue
            by_full: dict[str, list[str]] = defaultdict(list)
            for p in head_paths:
                digest = _hash(p)
                if digest:
                    by_full[digest].append(p)
            for full_paths in by_full.values():
                if len(full_paths) >= 2:
                    groups.append(DupeGroup(size, sorted(Path(p) for p in full_paths)))

    groups.sort(key=lambda g: g.wasted, reverse=True)
    return groups


@dataclass
class BigFile:
    path: Path
    size: int
    mtime: float


def find_big_files(root: Path, min_size: int, limit: int = 50) -> list[BigFile]:
    """The largest files under ``root`` that are at least ``min_size`` bytes."""
    found: list[BigFile] = []
    for entry in iter_files(root):
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_size >= min_size:
            found.append(BigFile(Path(entry.path), st.st_size, st.st_mtime))
    found.sort(key=lambda b: b.size, reverse=True)
    return found[:limit]

