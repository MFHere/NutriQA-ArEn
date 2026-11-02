"""OpenAI embeddings and approximate nearest-neighbor search."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

MODEL_NAME = "text-embedding-3-large"
MODEL_DIMENSIONS = 3072
MAX_INPUT_TOKENS = 8192
MAX_BATCH_ITEMS = 128
MAX_BATCH_TOKENS = 250_000
OPENAI_REQUEST_TIMEOUT_SECONDS = 60.0
OPENAI_MAX_RETRIES = 1
SEARCH_CHECKPOINT_INTERVAL = 256
SEARCH_VERSION = 1
ProgressCallback = Callable[[str, int, int | None], None]


@dataclass(frozen=True)
class EmbeddingGroup:
    group_id: int
    question: str


def cache_key(group: EmbeddingGroup) -> str:
    digest = hashlib.sha256(group.question.encode("utf-8")).hexdigest()
    return f"{MODEL_NAME}:{MODEL_DIMENSIONS}:{digest}"


def open_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS embeddings ("
        "cache_key TEXT PRIMARY KEY, vector BLOB NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS semantic_edges ("
        "left_group INTEGER NOT NULL, right_group INTEGER NOT NULL, "
        "similarity REAL NOT NULL, PRIMARY KEY (left_group, right_group))"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS semantic_progress ("
        "singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "run_key TEXT NOT NULL, next_position INTEGER NOT NULL, complete INTEGER NOT NULL)"
    )
    connection.commit()
    return connection


def _semantic_run_key(
    groups: Sequence[EmbeddingGroup],
    *,
    similarity_floor: float,
    top_k: int,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"semantic-search-v{SEARCH_VERSION}\n".encode("ascii"))
    digest.update(f"{MODEL_NAME}:{MODEL_DIMENSIONS}\n".encode("ascii"))
    digest.update(f"{similarity_floor:.17g}:{top_k}\n".encode("ascii"))
    for group in groups:
        digest.update(f"{group.group_id}:{cache_key(group)}\n".encode("ascii"))
    return digest.hexdigest()


def create_index(
    groups: Sequence[EmbeddingGroup],
    connection: sqlite3.Connection,
    *,
    on_progress: ProgressCallback | None = None,
) -> object:
    """Create a compact cosine index, filling missing embeddings through OpenAI."""
    try:
        import numpy as np
        import tiktoken
        from openai import OpenAI, OpenAIError
        from usearch.index import Index
    except ImportError as exc:
        raise RuntimeError(
            "Deduplication dependencies are missing. Reinstall the project with "
            "'python -m pip install -e .'."
        ) from exc

    if not groups:
        return Index(ndim=MODEL_DIMENSIONS, metric="cos", dtype="f16")

    try:
        encoding = tiktoken.encoding_for_model(MODEL_NAME)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")

    token_counts = [
        len(encoding.encode(group.question, disallowed_special=())) for group in groups
    ]
    for group, token_count in zip(groups, token_counts, strict=True):
        if token_count > MAX_INPUT_TOKENS:
            raise RuntimeError(
                f"Question at source row {group.group_id:,} has {token_count:,} tokens; "
                f"the {MODEL_NAME} input limit is {MAX_INPUT_TOKENS:,}. "
                "Shorten that question before semantic matching; it will not be truncated."
            )

    client: OpenAI | None = None
    index = Index(ndim=MODEL_DIMENSIONS, metric="cos", dtype="f16")
    batch: list[tuple[EmbeddingGroup, int]] = []
    batch_tokens = 0
    completed = 0

    def add_batch(items: list[tuple[EmbeddingGroup, int]]) -> None:
        nonlocal client, completed
        if not items:
            return

        vectors: list[object | None] = [None] * len(items)
        missing: list[tuple[int, EmbeddingGroup]] = []
        for position, (group, _) in enumerate(items):
            row = connection.execute(
                "SELECT vector FROM embeddings WHERE cache_key = ?",
                (cache_key(group),),
            ).fetchone()
            if row is None:
                missing.append((position, group))
            else:
                vector = np.frombuffer(row[0], dtype="<f2").astype(np.float32)
                if vector.size != MODEL_DIMENSIONS:
                    connection.execute(
                        "DELETE FROM embeddings WHERE cache_key = ?", (cache_key(group),)
                    )
                    missing.append((position, group))
                else:
                    vectors[position] = vector

        if missing:
            if client is None:
                api_key = os.environ.get("OPENAI_API_KEY")
                if not api_key:
                    raise RuntimeError(
                        "OPENAI_API_KEY is not set. Add it to the project .env file or "
                        "set it in the environment before "
                        "creating missing semantic embeddings."
                    )
                try:
                    client = OpenAI(
                        api_key=api_key,
                        timeout=OPENAI_REQUEST_TIMEOUT_SECONDS,
                        max_retries=OPENAI_MAX_RETRIES,
                    )
                except OpenAIError as exc:
                    raise RuntimeError(f"Could not initialize the OpenAI client: {exc}") from exc
            if on_progress is not None:
                on_progress(
                    f"Waiting for OpenAI embeddings ({len(missing):,} questions)",
                    completed,
                    len(groups),
                )
            try:
                response = client.embeddings.create(
                    model=MODEL_NAME,
                    input=[group.question for _, group in missing],
                    dimensions=MODEL_DIMENSIONS,
                )
            except OpenAIError as exc:
                if getattr(exc, "status_code", None) == 401:
                    raise RuntimeError(
                        "OpenAI rejected OPENAI_API_KEY. Check the key in the project .env "
                        "file or shell environment. A shell value takes precedence over .env."
                    ) from exc
                raise RuntimeError(
                    f"OpenAI embedding request failed: {exc} "
                    "Completed embedding batches are saved; rerun the same command to resume."
                ) from exc

            returned = sorted(response.data, key=lambda item: item.index)
            if len(returned) != len(missing):
                raise RuntimeError(
                    "OpenAI returned a different number of embeddings than questions sent."
                )

            for (position, group), item in zip(missing, returned, strict=True):
                vector = np.asarray(item.embedding, dtype=np.float32)
                if vector.size != MODEL_DIMENSIONS:
                    raise RuntimeError(
                        f"OpenAI returned {vector.size} dimensions; expected {MODEL_DIMENSIONS}."
                    )
                if not np.isfinite(vector).all():
                    raise RuntimeError("OpenAI returned an embedding containing a non-finite value.")
                vectors[position] = vector
                compressed = vector.astype("<f2").tobytes()
                connection.execute(
                    "INSERT OR REPLACE INTO embeddings (cache_key, vector) VALUES (?, ?)",
                    (cache_key(group), compressed),
                )
            connection.commit()

        if any(vector is None for vector in vectors):
            raise RuntimeError("An embedding was missing after cache and API processing.")

        matrix = np.vstack(vectors).astype(np.float32, copy=False)
        keys = np.asarray([group.group_id for group, _ in items], dtype=np.uint64)
        try:
            index.add(keys, matrix)
        except (ValueError, RuntimeError) as exc:
            raise RuntimeError(f"Could not add question vectors to the search index: {exc}") from exc

        completed += len(items)
        if on_progress is not None:
            on_progress(
                "Embedding and indexing questions with no exact duplicates",
                completed,
                len(groups),
            )

    for group, token_count in zip(groups, token_counts, strict=True):
        if batch and (
            len(batch) >= MAX_BATCH_ITEMS or batch_tokens + token_count > MAX_BATCH_TOKENS
        ):
            add_batch(batch)
            batch = []
            batch_tokens = 0
        batch.append((group, token_count))
        batch_tokens += token_count
    add_batch(batch)
    return index


def add_semantic_candidates(
    groups: Sequence[EmbeddingGroup],
    index: object,
    connection: sqlite3.Connection,
    *,
    similarity_floor: float,
    top_k: int,
    on_progress: ProgressCallback | None = None,
) -> int:
    """Store unique approximate-neighbor pairs whose cosine score meets the floor."""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("NumPy is required for semantic candidate search.") from exc

    run_key = _semantic_run_key(
        groups,
        similarity_floor=similarity_floor,
        top_k=top_k,
    )
    saved_progress = connection.execute(
        "SELECT run_key, next_position, complete FROM semantic_progress WHERE singleton = 1"
    ).fetchone()
    if (
        saved_progress is not None
        and saved_progress[0] == run_key
        and 0 <= saved_progress[1] <= len(groups)
    ):
        start_position = int(saved_progress[1])
        is_complete = bool(saved_progress[2])
    else:
        connection.execute("DELETE FROM semantic_edges")
        connection.execute(
            "INSERT OR REPLACE INTO semantic_progress "
            "(singleton, run_key, next_position, complete) VALUES (1, ?, 0, 0)",
            (run_key,),
        )
        connection.commit()
        start_position = 0
        is_complete = False

    if is_complete:
        return int(connection.execute("SELECT COUNT(*) FROM semantic_edges").fetchone()[0])

    key_to_group = {group.group_id: group for group in groups}
    total = len(groups)
    if on_progress is not None:
        phase = "Resuming semantic search" if start_position else "Finding semantic neighbors"
        on_progress(phase, start_position, total)

    for position in range(start_position, total):
        group = groups[position]
        row = connection.execute(
            "SELECT vector FROM embeddings WHERE cache_key = ?", (cache_key(group),)
        ).fetchone()
        if row is None:
            raise RuntimeError(
                f"Cached vector is missing for source row {group.group_id:,}."
            )
        vector = np.frombuffer(row[0], dtype="<f2").astype(np.float32)
        try:
            matches = index.search(vector, min(total, top_k + 1))
        except (ValueError, RuntimeError) as exc:
            raise RuntimeError(f"Could not search the semantic index: {exc}") from exc
        neighbors_seen = 0
        for match in matches:
            neighbor_id = int(match.key)
            if neighbor_id == group.group_id:
                continue
            if neighbors_seen >= top_k:
                break
            neighbors_seen += 1
            neighbor = key_to_group.get(neighbor_id)
            if neighbor is None:
                continue
            similarity = max(-1.0, min(1.0, 1.0 - float(match.distance)))
            if similarity < similarity_floor:
                continue

            left_id, right_id = sorted((group.group_id, neighbor_id))
            connection.execute(
                "INSERT INTO semantic_edges (left_group, right_group, similarity) "
                "VALUES (?, ?, ?) ON CONFLICT(left_group, right_group) DO UPDATE SET "
                "similarity = MAX(similarity, excluded.similarity)",
                (left_id, right_id, similarity),
            )

        completed = position + 1
        if completed % SEARCH_CHECKPOINT_INTERVAL == 0 or completed == total:
            connection.execute(
                "UPDATE semantic_progress SET next_position = ?, complete = ? "
                "WHERE singleton = 1",
                (completed, int(completed == total)),
            )
            connection.commit()
        if on_progress is not None:
            on_progress("Finding semantic neighbors", completed, total)

    if total == 0:
        connection.execute(
            "UPDATE semantic_progress SET next_position = 0, complete = 1 WHERE singleton = 1"
        )
        connection.commit()
    return int(connection.execute("SELECT COUNT(*) FROM semantic_edges").fetchone()[0])
