"""Command line interface: ``diskdiet <command>``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
)
from rich.table import Table

from . import __version__
from .cache import load_scan, save_scan
from .dupes import DupeGroup, find_big_files, find_duplicates
from .fsutil import age_seconds, human_age, human_size, parse_selection, parse_size
from .junk import JunkFolder, ScanProgress, scan_junk
from .tidy import apply_tidy, plan_tidy, undo_last

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)


# ------------------------------------------------------------------- helpers


def _short(path: Path, root: Path) -> str:
    try:
        rel = path.relative_to(root)
        return str(rel) if str(rel) != "." else str(path)
    except ValueError:
        return str(path)


def _ask_selection(count: int, prompt: str) -> list[int]:
    """Ask which numbered rows to act on. Re-asks on bad input."""
    if not sys.stdin.isatty():
        err.print("[yellow]Not running interactively: use --yes to select everything.[/]")
        return []
    while True:
        answer = console.input(prompt)
        try:
            return parse_selection(answer, count)
        except ValueError as exc:
            console.print(f"[red]{exc}[/] Try something like [bold]1,3,5-7[/], "
                          "[bold]all[/] or [bold]none[/].")


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return console.input(f"{question} [y/N] ").strip().lower() in {"y", "yes"}


def _trash(paths: Sequence[Path]) -> tuple[int, list[tuple[Path, str]]]:
    """Send paths to the Recycle Bin / Trash. Returns (ok_count, failures)."""
    from send2trash import send2trash

    ok = 0
    failed: list[tuple[Path, str]] = []
    for p in paths:
        try:
            send2trash(str(p))
            ok += 1
        except Exception as exc:  # send2trash raises several error types
            failed.append((p, str(exc)))
    return ok, failed


def _root(arg: str | None) -> Path:
    root = Path(arg).expanduser() if arg else Path.home()
    root = root.resolve()
    if not root.is_dir():
        err.print(f"[red]Not a folder:[/] {root}")
        raise SystemExit(2)
    return root


# -------------------------------------------------------------- junk folders


class _LiveScanProgress(ScanProgress):
    """Shows "Step 1 of 2: searching" then "Step 2 of 2: measuring" with live numbers."""

    def __init__(self, progress: Progress, root: Path) -> None:
        self.progress = progress
        self.root = root
        self.search = progress.add_task("Step 1 of 2: searching folders", total=None,
                                        info="", count="")
        self.measure: TaskID | None = None
        self._last = 0.0

    def searching(self, folder: str, folders_checked: int, found: int) -> None:
        self.checked, self.found = folders_checked, found
        now = time.monotonic()
        if now - self._last < 0.1:  # redrawing on every folder would slow us down
            return
        self._last = now
        where = _short(Path(folder), self.root)
        self.progress.update(
            self.search,
            info=f"{folders_checked:,} folders checked, {found} leftovers found "
                 f"[dim]{_clip(where)}[/]",
        )

    def finish_search(self) -> None:
        self.progress.update(
            self.search, total=1, completed=1,
            info=f"[green]done:[/] {self.checked:,} folders checked, "
                 f"{_plural(self.found, 'leftover')} found",
        )

    def measuring(self, done: int, total: int, folder: Path, bytes_so_far: int) -> None:
        if self.measure is None:
            self.finish_search()
            self.measure = self.progress.add_task("Step 2 of 2: measuring sizes",
                                                  total=total, info="", count="")
        self.progress.update(
            self.measure,
            completed=done,
            count=f"{done}/{total}",
            info=f"{human_size(bytes_so_far)} so far [dim]{_clip(_short(folder, self.root))}[/]",
        )


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _clip(text: str, width: int = 40) -> str:
    return text if len(text) <= width else "..." + text[-(width - 3):]


def _progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=24),
        TextColumn("{task.fields[count]}", style="cyan"),
        TextColumn("{task.fields[info]}"),
        console=console,
        transient=False,
    )


def _scan(root: Path, older_than: float) -> list[JunkFolder]:
    console.print(f"Looking for leftovers in [bold]{root}[/]")
    with _progress() as progress:
        live = _LiveScanProgress(progress, root)
        items = scan_junk(root, min_age_days=older_than, progress=live)
        if live.measure is None:  # nothing found, so step 2 never started
            live.finish_search()
    console.print()
    save_scan(root, older_than, items)
    return items


def _junk_table(items: list[JunkFolder], root: Path) -> Table:
    table = Table(show_edge=False, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Size", justify="right", style="bold cyan")
    table.add_column("What")
    table.add_column("Where")
    table.add_column("Project untouched", justify="right", style="dim")
    for i, item in enumerate(items, 1):
        table.add_row(
            str(i),
            human_size(item.size),
            item.kind,
            _short(item.path, root),
            human_age(item.project_age),
        )
    return table


def cmd_scan(args: argparse.Namespace) -> int:
    root = _root(args.path)
    if args.json:
        items = scan_junk(root, min_age_days=args.older_than)
        save_scan(root, args.older_than, items)
        print(json.dumps([
            {"path": str(i.path), "kind": i.kind, "bytes": i.size, "files": i.files,
             "project_untouched_days": round(i.project_age / 86400, 1)}
            for i in items
        ], indent=2))
        return 0
    items = _scan(root, args.older_than)
    if not items:
        console.print("[green]Nothing to clean here.[/] Your projects are tidy.")
        return 0
    console.print(_junk_table(items, root))
    total = sum(i.size for i in items)
    console.print(
        f"\n[bold]{_plural(len(items), 'folder')}, {human_size(total)} you could free.[/]"
    )
    console.print("All of these are re-created automatically when you next build or install.")
    if sys.stdin.isatty() and _confirm("\nPick some to clean now?"):
        return _clean_items(items, root, yes=False, dry_run=False)
    console.print("Run [bold]diskdiet clean[/] any time in the next hour to pick what to "
                  "remove, without scanning again.")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    root = _root(args.path)
    cached = None if args.rescan else load_scan(root, args.older_than)
    if cached is not None:
        items, age = cached
        console.print(f"Using your scan from {_ago(age)}. "
                      "[dim](Add --rescan to search again.)[/]\n")
    else:
        items = _scan(root, args.older_than)
    if not items:
        console.print("[green]Nothing to clean here.[/]")
        return 0
    console.print(_junk_table(items, root))
    total = sum(i.size for i in items)
    console.print(f"\n{_plural(len(items), 'folder')}, {human_size(total)} in total.")
    return _clean_items(items, root, yes=args.yes, dry_run=args.dry_run,
                        older_than=args.older_than)


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return "just now"
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"


def _clean_items(
    items: list[JunkFolder], root: Path, yes: bool, dry_run: bool, older_than: float = 0
) -> int:
    """Ask which of ``items`` to remove, then move them to the Trash."""
    if yes:
        picked = list(range(len(items)))
    else:
        picked = _ask_selection(len(items), "\nWhich ones? (e.g. 1,3,5-7 / all / none): ")
    chosen = [items[i] for i in picked]
    if not chosen:
        console.print("Nothing removed.")
        return 0
    size = sum(c.size for c in chosen)
    if dry_run:
        console.print(f"[yellow]Dry run:[/] would move {_plural(len(chosen), 'folder')} "
                      f"({human_size(size)}) to the Trash.")
        return 0
    if not yes and not _confirm(
        f"Move {_plural(len(chosen), 'folder')} ({human_size(size)}) to the Trash?"
    ):
        console.print("Nothing removed.")
        return 0

    failed: list[tuple[Path, str]] = []
    with _progress() as progress:
        task = progress.add_task("Moving to Trash", total=len(chosen), info="", count="")
        for n, c in enumerate(chosen, 1):
            progress.update(task, count=f"{n}/{len(chosen)}",
                            info=f"[dim]{_clip(_short(c.path, root))}[/]")
            _, fails = _trash([c.path])
            failed.extend(fails)
            progress.advance(task)
        progress.update(task, info="[green]done[/]")
    for path, reason in failed:
        err.print(f"[red]Could not remove[/] {path}: {reason}")
    failed_paths = {p for p, _ in failed}
    removed = [c for c in chosen if c.path not in failed_paths]
    freed = sum(c.size for c in removed)
    # Keep the remembered scan in step with what is still on disk.
    save_scan(root, older_than, [i for i in items if i not in removed])
    console.print(f"\n[bold green]Done. Freed about {human_size(freed)}[/] "
                  f"({_plural(len(removed), 'folder')} moved to the Trash).")
    console.print("[dim]Tip: empty your Trash / Recycle Bin to get the space back "
                  "for real, or restore from it if you change your mind.[/]")
    return 1 if failed else 0


# ---------------------------------------------------------------- duplicates


_DUPE_STEPS = {
    1: "Step 1 of 3: listing files",
    2: "Step 2 of 3: quick compare",
    3: "Step 3 of 3: full compare",
}


def _dupes_with_progress(root: Path, min_size: int) -> list[DupeGroup]:
    console.print(f"Looking for duplicate files over {human_size(min_size)} in [bold]{root}[/]")
    with _progress() as progress:
        tasks: dict[int, TaskID] = {}
        last = 0.0
        files_seen = 0

        def report(step: int, done: int, total: int, path: str) -> None:
            nonlocal last, files_seen
            if step == 1:
                files_seen = done
            if step not in tasks:
                for earlier_step, earlier in tasks.items():
                    msg = "[green]done[/]"
                    if earlier_step == 1:
                        msg += f": {_plural(files_seen, 'file')} checked"
                    progress.update(earlier, info=msg)
                tasks[step] = progress.add_task(_DUPE_STEPS[step], total=total or None,
                                                info="", count="")
            now = time.monotonic()
            finished = total and done >= total
            if not finished and now - last < 0.1:
                return
            last = now
            if step == 1:
                info = f"{done:,} files checked"
                if finished:
                    progress.update(tasks[1], total=total, completed=total)
            else:
                progress.update(tasks[step], total=total, completed=done,
                                count=f"{done}/{total}")
                info = ""
            if path:
                info += f" [dim]{_clip(_short(Path(path), root))}[/]"
            progress.update(tasks[step], info=info)

        groups = find_duplicates(root, min_size=min_size, on_progress=report)
        for step in (1, 2, 3):  # steps with nothing to do still show as done
            if step not in tasks:
                tasks[step] = progress.add_task(_DUPE_STEPS[step], total=1, info="", count="")
            task = next(t for t in progress.tasks if t.id == tasks[step])
            done_info = "[green]done[/]"
            if step == 1:
                done_info += f": {_plural(files_seen, 'file')} checked"
            progress.update(tasks[step], total=task.total or 1,
                            completed=task.total or 1, info=done_info)
    console.print()
    return groups


def cmd_dupes(args: argparse.Namespace) -> int:
    root = _root(args.path)
    min_size = parse_size(args.min_size)
    if args.json:
        groups = find_duplicates(root, min_size=min_size)
    else:
        groups = _dupes_with_progress(root, min_size)
    if args.json:
        print(json.dumps([
            {"bytes_each": g.size, "wasted_bytes": g.wasted, "keep": str(g.keeper()),
             "paths": [str(p) for p in g.paths]}
            for g in groups
        ], indent=2))
        return 0
    if not groups:
        console.print(f"[green]No duplicate files over {human_size(min_size)}.[/]")
        return 0

    shown = groups[: args.limit]
    for i, g in enumerate(shown, 1):
        keep = g.keeper()
        console.print(f"[dim]{i:>3}.[/] [bold cyan]{human_size(g.wasted)}[/] wasted, "
                      f"{len(g.paths)} copies of {human_size(g.size)}")
        for p in g.paths:
            mark = "[green]keep[/]  " if p == keep else "[red]extra[/] "
            console.print(f"       {mark}{_short(p, root)}")
    wasted = sum(g.wasted for g in groups)
    more = f" (showing the top {len(shown)})" if len(groups) > len(shown) else ""
    console.print(f"\n[bold]{_plural(len(groups), 'set')} of duplicates, {human_size(wasted)} "
                  f"wasted{more}.[/]")

    if not args.clean:
        console.print("Run with [bold]--clean[/] to move the extra copies to the Trash. "
                      "The oldest copy of each file is always kept.")
        return 0
    if args.yes:
        picked = list(range(len(shown)))
    else:
        picked = _ask_selection(len(shown), "\nRemove extras for which sets? "
                                            "(e.g. 1,3 / all / none): ")
    extras = [p for i in picked for p in shown[i].paths if p != shown[i].keeper()]
    if not extras:
        console.print("Nothing removed.")
        return 0
    size = sum(shown[i].wasted for i in picked)
    if args.dry_run:
        console.print(f"[yellow]Dry run:[/] would move {len(extras)} files "
                      f"({human_size(size)}) to the Trash.")
        return 0
    if not args.yes and not _confirm(f"Move {len(extras)} extra copies "
                                     f"({human_size(size)}) to the Trash?"):
        console.print("Nothing removed.")
        return 0
    ok, failed = _trash(extras)
    for path, reason in failed:
        err.print(f"[red]Could not remove[/] {path}: {reason}")
    console.print(f"[bold green]Done.[/] {ok} extra copies moved to the Trash.")
    return 1 if failed else 0


# ----------------------------------------------------------------- big files


def cmd_big(args: argparse.Namespace) -> int:
    root = _root(args.path)
    min_size = parse_size(args.min_size)
    with console.status(f"Looking for big files in {root} ...") as status:
        files = find_big_files(
            root, min_size, limit=args.limit,
            on_progress=lambda n, p: status.update(
                f"Looking for big files: {n:,} files checked "
                f"[dim]{_clip(_short(Path(p), root))}[/]"
            ),
        )
    if args.json:
        print(json.dumps([{"path": str(f.path), "bytes": f.size} for f in files], indent=2))
        return 0
    if not files:
        console.print(f"[green]No files over {human_size(min_size)}.[/]")
        return 0
    table = Table(show_edge=False, header_style="bold")
    table.add_column("Size", justify="right", style="bold cyan")
    table.add_column("File")
    table.add_column("Last changed", justify="right", style="dim")
    for f in files:
        table.add_row(human_size(f.size), _short(f.path, root), human_age(age_seconds(f.mtime)))
    console.print(table)
    console.print(f"\n[bold]{len(files)} files, {human_size(sum(f.size for f in files))}.[/] "
                  "diskdiet never deletes big files for you: review them yourself.")
    return 0


# ---------------------------------------------------------------- tidy/undo


def _downloads() -> Path:
    return Path.home() / "Downloads"


def cmd_tidy(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve() if args.path else _downloads()
    if not root.is_dir():
        err.print(f"[red]Not a folder:[/] {root}")
        return 2
    plan = plan_tidy(root, older_than_days=args.older_than)
    if not plan.moves:
        console.print(f"[green]{root} is already tidy.[/]")
        return 0
    table = Table(show_edge=False, header_style="bold")
    table.add_column("Folder", style="bold")
    table.add_column("Files", justify="right", style="cyan")
    table.add_column("For example", style="dim")
    for cat, moves in plan.by_category().items():
        sample = ", ".join(m.src.name for m in moves[:3])
        if len(moves) > 3:
            sample += ", ..."
        table.add_row(cat + "/", str(len(moves)), sample)
    console.print(f"Plan for [bold]{root}[/]:\n")
    console.print(table)
    console.print(f"\n{len(plan.moves)} files would be sorted into folders. "
                  "Folders and files still downloading are left alone.")
    if args.dry_run:
        console.print("[yellow]Dry run:[/] nothing was moved.")
        return 0
    if not args.yes and not _confirm("Go ahead?"):
        console.print("Nothing moved.")
        return 0
    journal, done = apply_tidy(plan)
    console.print(f"[bold green]Done.[/] Sorted {len(done)} files. "
                  "Changed your mind? Run [bold]diskdiet undo[/].")
    console.print(f"[dim]Undo record: {journal}[/]")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    result = undo_last()
    if result.journal is None:
        console.print("Nothing to undo.")
        return 0
    console.print(f"[bold green]Put back {result.restored} files.[/]")
    for note in result.skipped:
        console.print(f"[yellow]Skipped:[/] {note}")
    return 0


# ------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="diskdiet",
        description="Free up disk space safely. Nothing is deleted without asking, "
                    "and removed items go to your Trash / Recycle Bin.",
    )
    parser.add_argument("--version", action="version", version=f"diskdiet {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add_path(p: argparse.ArgumentParser, default: str) -> None:
        p.add_argument("path", nargs="?", help=f"folder to look in (default: {default})")

    def add_age(p: argparse.ArgumentParser) -> None:
        p.add_argument("--older-than", type=float, default=0, metavar="DAYS",
                       help="only projects untouched for this many days")

    p = sub.add_parser("scan", help="show coding leftovers you can safely remove")
    add_path(p, "your home folder")
    add_age(p)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("clean", help="choose leftovers to move to the Trash")
    add_path(p, "your home folder")
    add_age(p)
    p.add_argument("--dry-run", action="store_true", help="show what would happen only")
    p.add_argument("-y", "--yes", action="store_true", help="select everything, no questions")
    p.add_argument("--rescan", action="store_true",
                   help="search again instead of reusing a scan from the last hour")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("dupes", help="find duplicate files")
    add_path(p, "your home folder")
    p.add_argument("--min-size", default="1MB", help="ignore smaller files (default: 1MB)")
    p.add_argument("--limit", type=int, default=30, help="how many sets to show (default: 30)")
    p.add_argument("--clean", action="store_true", help="offer to trash the extra copies")
    p.add_argument("--dry-run", action="store_true", help="with --clean: show only")
    p.add_argument("-y", "--yes", action="store_true", help="with --clean: no questions")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_dupes)

    p = sub.add_parser("big", help="list the biggest files")
    add_path(p, "your home folder")
    p.add_argument("--min-size", default="500MB", help="default: 500MB")
    p.add_argument("--limit", type=int, default=30, help="default: 30")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_big)

    p = sub.add_parser("tidy", help="sort a messy folder (default: Downloads) into folders")
    add_path(p, "your Downloads folder")
    p.add_argument("--older-than", type=float, default=0, metavar="DAYS",
                   help="only move files older than this")
    p.add_argument("--dry-run", action="store_true", help="show the plan only")
    p.add_argument("-y", "--yes", action="store_true", help="no questions")
    p.set_defaults(func=cmd_tidy)

    p = sub.add_parser("undo", help="put back the files from the last tidy")
    p.set_defaults(func=cmd_undo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["scan", *(argv or sys.argv[1:])])
    try:
        return args.func(args)
    except ValueError as exc:
        err.print(f"[red]{exc}[/]")
        return 2
    except KeyboardInterrupt:
        err.print("\nStopped. Nothing else was changed.")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
