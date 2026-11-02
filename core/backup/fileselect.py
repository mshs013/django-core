"""File backup: source selection, exclusion rules, tar.gz creation, validation and safe extraction. Pure Python (stdlib only).

Safety model
* Only roots listed in ``BACKUP_SOURCE_PATHS`` can be backed up. Callers pick a source by INDEX; the path is re-derived from
  configuration, user text never becomes a path.
* Per-job extra exclusions are relative to the chosen root and must stay inside it.
* Symbolic links are never followed. A link is stored only when it is RELATIVE and resolves inside the root; everything else
  is skipped and reported, so an archive can neither leak nor (when restored) point outside its restore directory.
* BACKUP_DIR (and the archive dir) are always excluded, and a source that is or contains them is refused.
* Archive member names are relative (``<root name>/<relative path>``); extraction validates every member again.
"""
from __future__ import annotations

import fnmatch
import os
import stat
import tarfile
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

HIDDEN_MESSAGE = "hidden file/folder (hidden files are not included)"
MAX_NOTES = 50


class FileBackupError(Exception):
    """Selection, archiving, validation or extraction problem. Messages are safe to show."""


@dataclass(frozen=True)
class Source:
    index: int
    label: str
    path: Path


def parse_sources(entries: Iterable[str]) -> list[Source]:
    """``BACKUP_SOURCE_PATHS`` entries: ``/path`` or ``Label=/path``. Duplicate paths are dropped; blank entries ignored."""
    sources: list[Source] = []
    seen: set[Path] = set()
    for raw in entries:
        text = (raw or "").strip()
        if not text:
            continue
        label = ""
        if "=" in text and not text.split("=", 1)[0].strip().startswith(("/", "~", ".")):
            label, text = (part.strip() for part in text.split("=", 1))
        path = Path(text).expanduser().resolve(strict=False)
        if path in seen:
            continue
        seen.add(path)
        sources.append(Source(len(sources), label or path.name or str(path), path))
    return sources


def pick_source(sources: Sequence[Source], index: object) -> Source:
    try:
        number = int(index)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise FileBackupError("Choose one of the configured backup sources.") from None
    for source in sources:
        if source.index == number:
            return source
    raise FileBackupError("Choose one of the configured backup sources.")


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def refuse_overlap_with_storage(root: Path, storage_dirs: Iterable[Path]) -> None:
    """A source that is (or lies inside) the backup storage would archive backups inside backups. A source that merely CONTAINS the
    storage is fine: the storage folder is excluded automatically."""
    for folder in storage_dirs:
        if _inside(root, folder.resolve(strict=False)):
            raise FileBackupError("This source is inside the backup storage folder; backups never include backups.")


def parse_extra_exclusions(text: str) -> list[str]:
    """Split user text (one per line or comma separated) into entries; drops blanks, rejects NUL and absolute/parent-escaping paths."""
    entries = []
    for part in text.replace(",", "\n").splitlines():
        part = part.strip()
        if not part:
            continue
        if "\x00" in part:
            raise FileBackupError("Invalid exclusion entry.")
        entries.append(part)
    return entries


@dataclass
class ExclusionRules:
    root: Path
    paths: list[Path] = field(default_factory=list)       # absolute, resolved
    patterns: list[str] = field(default_factory=list)
    include_hidden: bool = True

    @classmethod
    def build(cls, root: Path, *, config_paths: Iterable[str] = (), config_patterns: Iterable[str] = (), extra: Iterable[str] = (),
              storage_dirs: Iterable[Path] = (), include_hidden: bool = True) -> "ExclusionRules":
        """``config_*`` come from settings, ``extra`` from the job. A path-like extra (contains '/' and no wildcard) is a path relative
        to the root and must stay inside it; anything else is a wildcard pattern."""
        paths = [Path(p).expanduser().resolve(strict=False) for p in config_paths if str(p).strip()]
        paths += [Path(d).resolve(strict=False) for d in storage_dirs]
        patterns = [p.strip() for p in config_patterns if str(p).strip()]
        for item in extra:
            if any(ch in item for ch in "*?[") or "/" not in item:
                patterns.append(item)
                continue
            candidate = (root / item.lstrip("/")).resolve(strict=False)
            if not _inside(candidate, root):
                raise FileBackupError(f"Exclusion '{item}' is outside the selected source.")
            paths.append(candidate)
        return cls(root=root, paths=paths, patterns=patterns, include_hidden=include_hidden)

    def reason(self, path: Path, is_root: bool = False) -> str | None:
        for excluded in self.paths:
            if _inside(path, excluded):
                return "excluded path"
        if not is_root and not self.include_hidden and path.name.startswith("."):
            return HIDDEN_MESSAGE
        rel = path.relative_to(self.root).as_posix() if _inside(path, self.root) and path != self.root else path.name
        for pattern in self.patterns:
            if fnmatch.fnmatch(path.name, pattern):
                return f"pattern {pattern}"
            if "/" in pattern and (fnmatch.fnmatch(rel, pattern.lstrip("/")) or fnmatch.fnmatch(path.as_posix(), pattern)):
                return f"pattern {pattern}"
        return None


def arcname_for(root: Path, path: Path) -> str:
    base = root.name or "root"
    rel = path.relative_to(root).as_posix()
    return base if rel == "." else f"{base}/{rel}"


def link_is_safe(path: Path, root: Path) -> bool:
    """Relative link whose target resolves inside root."""
    try:
        target = os.readlink(path)
    except OSError:
        return False
    if os.path.isabs(target):
        return False
    return _inside((path.parent / target).resolve(strict=False), root)


@dataclass
class Entry:
    path: Path
    arcname: str
    kind: str          # "file" | "dir" | "link"
    size: int = 0


def new_stats(root: Path) -> dict:
    return {"root": str(root), "files": 0, "dirs": 0, "symlinks": 0, "bytes": 0, "excluded": 0, "excluded_examples": [],
            "skipped_symlinks": [], "skipped_special": [], "errors": []}


def _note(stats: dict, key: str, value: str) -> None:
    if len(stats[key]) < MAX_NOTES:
        stats[key].append(value)


def iter_entries(root: Path, rules: ExclusionRules, stats: dict) -> Iterator[Entry]:
    """Walk ``root`` without following links, applying exclusions (excluded folders are pruned, never entered)."""
    if not os.path.lexists(root):
        raise FileBackupError("The configured source does not exist or is not visible to the worker.")
    if root.is_symlink() or not root.is_dir():
        raise FileBackupError("The configured source must be a real directory.")
    reason = rules.reason(root, is_root=True)
    if reason:
        raise FileBackupError("The whole source is excluded by an exclusion rule.")
    yield Entry(root, arcname_for(root, root), "dir")
    for current, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: _note(stats, "errors", e.strerror or "unreadable folder")):
        base = Path(current)
        kept = []
        for name in sorted(dirs):
            child = base / name
            why = rules.reason(child)
            if why:
                stats["excluded"] += 1
                _note(stats, "excluded_examples", f"{arcname_for(root, child)} ({why})")
            elif child.is_symlink():
                if link_is_safe(child, root):
                    yield Entry(child, arcname_for(root, child), "link")
                else:
                    _note(stats, "skipped_symlinks", arcname_for(root, child))
            else:
                kept.append(name)
                yield Entry(child, arcname_for(root, child), "dir")
        dirs[:] = kept
        for name in sorted(files):
            child = base / name
            why = rules.reason(child)
            if why:
                stats["excluded"] += 1
                _note(stats, "excluded_examples", f"{arcname_for(root, child)} ({why})")
                continue
            try:
                st = child.lstat()
            except OSError as exc:
                _note(stats, "errors", f"{arcname_for(root, child)}: {exc.strerror or exc}")
                continue
            if stat.S_ISLNK(st.st_mode):
                if link_is_safe(child, root):
                    yield Entry(child, arcname_for(root, child), "link")
                else:
                    _note(stats, "skipped_symlinks", arcname_for(root, child))
            elif stat.S_ISREG(st.st_mode):
                yield Entry(child, arcname_for(root, child), "file", st.st_size)
            else:
                _note(stats, "skipped_special", arcname_for(root, child))


def build_archive(dest: Path, root: Path, rules: ExclusionRules, *, compression_level: int = 6,
                  progress: Callable[[int, str], None] | None = None, should_cancel: Callable[[], bool] | None = None) -> dict:
    """Write ``dest`` (tar.gz) from ``root``. Returns the stats dict (manifest material). Blocking I/O."""
    stats = new_stats(root)
    total_bytes = sum(e.size for e in iter_entries(root, rules, new_stats(root)))
    done_bytes = 0
    last_pct = -1
    with tarfile.open(dest, "w:gz", compresslevel=max(1, min(9, compression_level))) as tar:
        for entry in iter_entries(root, rules, stats):
            if should_cancel is not None and should_cancel():
                raise FileBackupError("Cancelled")
            try:
                tar.add(entry.path, arcname=entry.arcname, recursive=False)
            except OSError as exc:       # file vanished or unreadable while archiving
                _note(stats, "errors", f"{entry.arcname}: {exc.strerror or exc}")
                continue
            if entry.kind == "file":
                stats["files"] += 1
                stats["bytes"] += entry.size
                done_bytes += entry.size
            elif entry.kind == "dir":
                stats["dirs"] += 1
            else:
                stats["symlinks"] += 1
            if progress is not None and total_bytes:
                pct = min(99, int(done_bytes * 100 / total_bytes))
                if pct != last_pct:
                    last_pct = pct
                    progress(pct, f"{stats['files']} files archived")
    if stats["files"] == 0:
        raise FileBackupError("No files were backed up: the source has no files after exclusions, or they are unreadable by the worker user.")
    return stats


def member_problem(member: tarfile.TarInfo) -> str | None:
    """Why an archive member is unsafe to extract, or None."""
    name = member.name
    parts = Path(name).parts
    if name.startswith("/") or ".." in parts or "\x00" in name:
        return f"unsafe path {name!r}"
    if member.isdev() or member.isfifo():
        return f"special file {name!r}"
    if member.issym() or member.islnk():
        target = member.linkname
        if member.islnk():
            return f"hard link {name!r} is not allowed"
        if os.path.isabs(target) or not _link_stays_inside(name, target):
            return f"link {name!r} points outside the archive"
    return None


def _link_stays_inside(name: str, target: str) -> bool:
    """A link must stay within the archive's top-level folder (the source root)."""
    top = Path(name).parts[0]
    resolved = os.path.normpath(os.path.join(os.path.dirname(name), target))
    return resolved == top or resolved.startswith(top + "/")


def validate_archive(path: Path, expected_files: int | None = None) -> str:
    """Read every member and every byte; raises FileBackupError on corruption, unsafe names or a file-count mismatch."""
    files = dirs = links = 0
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                problem = member_problem(member)
                if problem:
                    raise FileBackupError(f"Archive rejected: {problem}")
                files += member.isreg()
                dirs += member.isdir()
                links += member.issym()
                if member.isreg():
                    handle = tar.extractfile(member)
                    while handle.read(1 << 20):
                        pass
    except FileBackupError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise FileBackupError(f"Not a readable archive: {type(exc).__name__}") from None
    if expected_files is not None and files != expected_files:
        raise FileBackupError(f"{files} files in the archive but {expected_files} were recorded at backup time")
    return f"ok: {files} files, {dirs} folders, {links} links; every file readable"


def safe_extract(archive: Path, target: Path) -> int:
    """Extract into the (new, empty) ``target`` directory after checking every member. Returns the number of regular files."""
    target = target.resolve(strict=True)
    count = 0
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            problem = member_problem(member)
            if problem:
                raise FileBackupError(f"Archive rejected: {problem}")
            destination = (target / member.name).resolve(strict=False)
            if not destination.is_relative_to(target):
                raise FileBackupError(f"Archive rejected: {member.name!r} escapes the restore folder")
            count += member.isreg()
        tar.extractall(target, members=members, filter="data")
    return count
