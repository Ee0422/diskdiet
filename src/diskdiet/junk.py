"""Find folders that coding tools re-create on demand (node_modules, venvs, caches...).

Every rule needs *evidence* before it matches. A folder called ``build`` is
only reported when its parent also has a ``build.gradle``; a ``.venv`` only
when it contains ``pyvenv.cfg``. That keeps us from flagging a folder that
merely happens to share a name.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .fsutil import age_seconds, dir_size, should_skip_dir

Check = Callable[[Path, Path], bool]


def _parent_has(*names: str) -> Check:
    def check(folder: Path, parent: Path) -> bool:
        return any((parent / n).exists() for n in names)

    return check


def _parent_glob(pattern: str) -> Check:
    def check(folder: Path, parent: Path) -> bool:
        return any(parent.glob(pattern))

    return check


def _contains(*names: str) -> Check:
    def check(folder: Path, parent: Path) -> bool:
        return any((folder / n).exists() for n in names)

    return check


def _always(folder: Path, parent: Path) -> bool:
    return True


@dataclass(frozen=True)
class Rule:
    name: str  # folder name to match
    kind: str  # shown to the user
    check: Check


_JS = _parent_has("package.json")

RULES: tuple[Rule, ...] = (
    # JavaScript / TypeScript
    Rule("node_modules", "Node.js packages", _JS),
    Rule(".next", "Next.js build", _JS),
    Rule(".nuxt", "Nuxt build", _JS),
    Rule(".svelte-kit", "SvelteKit build", _JS),
    Rule(".turbo", "Turborepo cache", _JS),
    Rule(".parcel-cache", "Parcel cache", _JS),
    Rule(".angular", "Angular cache", _parent_has("angular.json")),
    # Python
    Rule(".venv", "Python virtualenv", _contains("pyvenv.cfg")),
    Rule("venv", "Python virtualenv", _contains("pyvenv.cfg")),
    Rule("env", "Python virtualenv", _contains("pyvenv.cfg")),
    Rule("__pycache__", "Python bytecode", _always),
    Rule(".pytest_cache", "pytest cache", _always),
    Rule(".mypy_cache", "mypy cache", _always),
    Rule(".ruff_cache", "Ruff cache", _always),
    Rule(".tox", "tox environments", _parent_has("tox.ini", "pyproject.toml", "setup.py")),
    Rule(".nox", "nox environments", _parent_has("noxfile.py")),
    # Rust / Java / Kotlin
    Rule("target", "Rust build", _parent_has("Cargo.toml")),
    Rule("target", "Maven build", _parent_has("pom.xml")),
    Rule("build", "Gradle build", _parent_has("build.gradle", "build.gradle.kts")),
    Rule(".gradle", "Gradle cache", _parent_has("build.gradle", "build.gradle.kts")),
    # .NET
    Rule("bin", ".NET build", _parent_glob("*.csproj")),
    Rule("obj", ".NET build", _parent_glob("*.csproj")),
    # Mobile
    Rule("Pods", "CocoaPods", _parent_has("Podfile")),
    Rule(".dart_tool", "Dart/Flutter tools", _parent_has("pubspec.yaml")),
    Rule("build", "Flutter build", _parent_has("pubspec.yaml")),
    # Others
    Rule("_build", "Elixir build", _parent_has("mix.exs")),
    Rule("deps", "Elixir deps", _parent_has("mix.exs")),
    Rule(".terraform", "Terraform providers", _parent_glob("*.tf")),
    Rule("zig-cache", "Zig cache", _parent_has("build.zig")),
    Rule(".zig-cache", "Zig cache", _parent_has("build.zig")),
    Rule("Library", "Unity cache", _parent_has("ProjectSettings")),
)

_BY_NAME: dict[str, list[Rule]] = {}
for _rule in RULES:
    _BY_NAME.setdefault(_rule.name, []).append(_rule)


@dataclass
class JunkFolder:
    path: Path
    kind: str
    size: int
    files: int
    project_age: float  # seconds since anything in the project changed

    @property
    def project(self) -> Path:
        return self.path.parent


def match_rule(folder: Path) -> Rule | None:
    """Return the rule that claims ``folder``, if any."""
    for rule in _BY_NAME.get(folder.name, ()):
        try:
            if rule.check(folder, folder.parent):
                return rule
        except OSError:
            continue
    return None


def project_last_touched(project: Path, ignore: Path) -> float:
    """Newest modification time among the project's top-level entries."""
    newest = 0.0
    try:
        with os.scandir(project) as entries:
            for entry in entries:
                if entry.path == str(ignore):
                    continue
                # Other junk folders (e.g. __pycache__) don't count as activity.
                if entry.name in _BY_NAME:
                    continue
                try:
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return newest


def find_junk_paths(root: Path) -> Iterator[tuple[Path, Rule]]:
    """Walk ``root`` and yield every matching folder (without measuring it)."""
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                subdirs = [e for e in entries if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for entry in subdirs:
            if should_skip_dir(entry, current) and entry.name not in _BY_NAME:
                continue
            folder = Path(entry.path)
            rule = match_rule(folder)
            if rule is not None:
                yield folder, rule
                continue  # never look inside a junk folder
            if should_skip_dir(entry, current):
                continue
            stack.append(entry.path)


def scan_junk(
    root: Path,
    min_age_days: float = 0,
    on_found: Callable[[Path], None] | None = None,
) -> list[JunkFolder]:
    """Find and measure junk folders, biggest first.

    ``min_age_days`` keeps only projects nobody has touched for that long.
    """
    results: list[JunkFolder] = []
    for folder, rule in find_junk_paths(root):
        if on_found:
            on_found(folder)
        last = project_last_touched(folder.parent, folder)
        age = age_seconds(last) if last else 0.0
        if age < min_age_days * 86400:
            continue
        size, files = dir_size(folder)
        if size == 0:
            continue
        results.append(JunkFolder(folder, rule.kind, size, files, age))
    results.sort(key=lambda j: j.size, reverse=True)
    return results
