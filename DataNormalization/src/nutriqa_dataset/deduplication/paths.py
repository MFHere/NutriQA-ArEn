"""Versioned default paths for deduplication runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class DeduplicationPaths:
    version: int
    review: Path
    output: Path
    audit: Path


def _dataset_lineage(input_path: Path) -> tuple[str, int]:
    """Return the shared dataset name and version represented by an input file."""
    match = re.fullmatch(r"(?P<root>.+?)(?:-v(?P<version>\d+))?-deduplicated", input_path.stem)
    if match is None:
        return input_path.stem, 0
    return match.group("root"), int(match.group("version") or 1)


def _artifact_version(path: Path, root: str) -> int | None:
    markers = ("-dedup-review", "-deduplicated", "-dedup-audit")
    for marker in markers:
        if path.stem == f"{root}{marker}":
            return 1
        match = re.fullmatch(
            rf"{re.escape(root)}-v(?P<version>\d+){re.escape(marker)}",
            path.stem,
        )
        if match is not None:
            return int(match.group("version"))
    return None


def paths_for_version(directory: Path, root: str, version: int) -> DeduplicationPaths:
    if version < 1:
        raise ValueError("A deduplication run version must be positive.")
    prefix = root if version == 1 else f"{root}-v{version}"
    return DeduplicationPaths(
        version=version,
        review=directory / f"{prefix}-dedup-review.json",
        output=directory / f"{prefix}-deduplicated.json",
        audit=directory / f"{prefix}-dedup-audit.json",
    )


def next_run_paths(input_path: Path) -> DeduplicationPaths:
    """Choose the next unused shared version for all run artifacts."""
    root, input_version = _dataset_lineage(input_path)
    highest_version = input_version
    if input_path.parent.exists():
        for path in input_path.parent.iterdir():
            version = _artifact_version(path, root) if path.suffix.casefold() == ".json" else None
            if version is not None:
                highest_version = max(highest_version, version)

    version = highest_version + 1
    paths = paths_for_version(input_path.parent, root, version)
    while any(path.exists() for path in (paths.review, paths.output, paths.audit)):
        version += 1
        paths = paths_for_version(input_path.parent, root, version)
    return paths


def pending_run_paths(input_path: Path) -> DeduplicationPaths | None:
    """Find the newest review after the input version with no output or audit yet."""
    root, input_version = _dataset_lineage(input_path)
    versions = {
        version
        for path in input_path.parent.iterdir()
        if path.suffix.casefold() == ".json"
        if (version := _artifact_version(path, root)) is not None
        and version > input_version
    }
    for version in sorted(versions, reverse=True):
        paths = paths_for_version(input_path.parent, root, version)
        if paths.review.is_file() and not paths.output.exists() and not paths.audit.exists():
            return paths
    return None


def paths_for_review(input_path: Path, review_path: Path) -> DeduplicationPaths:
    """Match a conventional review filename to its output and audit names."""
    root, _ = _dataset_lineage(input_path)
    version = _artifact_version(review_path, root)
    if version is None:
        return next_run_paths(input_path)
    return paths_for_version(input_path.parent, root, version)


def latest_review_path(directory: Path, root: str) -> Path | None:
    """Return the highest-version review file for a dataset lineage."""
    if not directory.is_dir():
        return None
    versions = {
        version
        for path in directory.iterdir()
        if path.suffix.casefold() == ".json"
        if (version := _artifact_version(path, root)) is not None
        and path.name.endswith("-dedup-review.json")
    }
    for version in sorted(versions, reverse=True):
        paths = paths_for_version(directory, root, version)
        if paths.review.is_file():
            return paths.review
    return None
