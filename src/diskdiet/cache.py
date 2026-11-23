"""Remember the last scan so ``diskdiet clean`` doesn't have to search again."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .fsutil import data_dir
from .junk import JunkFolder

# A scan older than this is considered stale and is redone.
MAX_AGE_SECONDS = 60 * 60


def _cache_file() -> Path:
    return data_dir() / "last-scan.json"


def save_scan(root: Path, min_age_days: float, items: list[JunkFolder]) -> None:
    try:
        path = _cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "root": str(root),
                    "min_age_days": min_age_days,
                    "created": time.time(),
                    "items": [
                        {"path": str(i.path), "kind": i.kind, "size": i.size,
                         "files": i.files, "project_age": i.project_age}
                        for i in items
                    ],
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # caching is a nice-to-have; never fail a scan over it


def load_scan(
    root: Path, min_age_days: float, max_age: float = MAX_AGE_SECONDS
) -> tuple[list[JunkFolder], float] | None:
    """Return ``(items, seconds_since_scan)`` if a fresh scan of ``root`` exists.

    A scan made with a smaller ``--older-than`` can be reused by filtering it.
    Folders that have disappeared since are dropped.
    """
    try:
        data = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    age = time.time() - float(data.get("created", 0))
    if data.get("root") != str(root) or not 0 <= age <= max_age:
        return None
    if float(data.get("min_age_days", 0)) > min_age_days:
        return None
    items: list[JunkFolder] = []
    for raw in data.get("items", []):
        # project_age was measured at scan time; it has only grown since.
        project_age = float(raw["project_age"]) + age
        if project_age < min_age_days * 86400:
            continue
        path = Path(raw["path"])
        if not path.exists():
            continue
        items.append(JunkFolder(path, raw["kind"], int(raw["size"]), int(raw["files"]),
                                project_age))
    return items, age


def forget_scan() -> None:
    try:
        _cache_file().unlink()
    except OSError:
        pass
