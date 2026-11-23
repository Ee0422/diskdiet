"""Find duplicate files and very large files.

Duplicates are found in three cheap-to-expensive passes so we rarely read
whole files: group by size, then by a hash of the first 64 KB, then by a
full hash. Only files that survive every pass are reported.
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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


# on_progress(step, done, total, current_path); total is 0 while still unknown.
Progress = Callable[[int, int, int, str], None]


def _noop(step: int, done: int, total: int, path: str) -> None:
    pass


def find_duplicates(
    root: Path,
    min_size: int = 1024 * 1024,
    on_progress: Progress | None = None,
) -> list[DupeGroup]:
    """Return groups of identical files under ``root``, most wasted space first.

    Reports progress in three steps: 1 list files, 2 quick compare, 3 full compare.
    """
    report = on_progress or _noop

    # Step 1: list files, grouped by size
    by_size: dict[int, list[str]] = defaultdict(list)
    seen_inodes: set[tuple[int, int]] = set()
    seen = 0
    for entry in iter_files(root):
        seen += 1
        if seen % 200 == 0:
            report(1, seen, 0, entry.path)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_size < min_size:
            continue
        # Hard links already share their data; they are not real duplicates.
        if not st.st_ino:
            # On Windows, scandir leaves the file ID empty; a full stat fills it in.
            try:
                st = os.stat(entry.path, follow_symlinks=False)
            except OSError:
                continue
        if st.st_ino:
            key = (st.st_dev, st.st_ino)
            if key in seen_inodes:
                continue
            seen_inodes.add(key)
        by_size[st.st_size].append(entry.path)
    report(1, seen, seen, "")

    # Step 2: same size -> compare the first 64 KB
    same_size = [(size, paths) for size, paths in by_size.items() if len(paths) >= 2]
    total = sum(len(paths) for _, paths in same_size)
    done = 0
    head_groups: list[tuple[int, list[str]]] = []
    for size, paths in same_size:
        by_head: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            digest = _hash(p, _HEAD)
            done += 1
            report(2, done, total, p)
            if digest:
                by_head[digest].append(p)
        head_groups.extend((size, hp) for hp in by_head.values() if len(hp) >= 2)

    # Step 3: same start -> compare whole files
    groups: list[DupeGroup] = []
    need_full = [(size, hp) for size, hp in head_groups if size > _HEAD]
    total = sum(len(hp) for _, hp in need_full)
    done = 0
    for size, head_paths in head_groups:
        if size <= _HEAD:
            # The head hash already covered the whole file.
            groups.append(DupeGroup(size, sorted(Path(p) for p in head_paths)))
            continue
        by_full: dict[str, list[str]] = defaultdict(list)
        for p in head_paths:
            digest = _hash(p)
            done += 1
            report(3, done, total, p)
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


def find_big_files(
    root: Path,
    min_size: int,
    limit: int = 50,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[BigFile]:
    """The largest files under ``root`` that are at least ``min_size`` bytes."""
    found: list[BigFile] = []
    for n, entry in enumerate(iter_files(root), 1):
        if on_progress and n % 200 == 0:
            on_progress(n, entry.path)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if st.st_size >= min_size:
            found.append(BigFile(Path(entry.path), st.st_size, st.st_mtime))
    found.sort(key=lambda b: b.size, reverse=True)
    return found[:limit]

