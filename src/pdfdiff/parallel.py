"""Running independent page-range jobs in worker processes."""

from __future__ import annotations

import math
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable

_MAX_JOBS = 8


def worker_count(jobs: int | None, items: int, min_chunk: int) -> int:
    """Number of workers to use for ``items`` units of work (1 = run in-process)."""
    if jobs is None:
        jobs = min(os.cpu_count() or 1, _MAX_JOBS)
    if not items or not _can_spawn():
        return 1
    return max(1, min(jobs, math.ceil(items / min_chunk)))


def chunks(n: int, workers: int, min_chunk: int) -> list[tuple[int, int]]:
    """Split range(n) into [start, stop) ranges, several per worker to balance load."""
    size = max(min_chunk, math.ceil(n / (workers * 4)))
    return [(s, min(s + size, n)) for s in range(0, n, size)]


def run(fn: Callable[..., Any], tasks: list[tuple], workers: int) -> list[Any] | None:
    """``[fn(*t) for t in tasks]`` in worker processes, or None if that isn't possible
    (the caller then does the work itself)."""
    try:
        # "spawn" avoids forking a process that may have threads or MuPDF state.
        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futures = [pool.submit(fn, *t) for t in tasks]
            return [f.result() for f in futures]
    except (OSError, RuntimeError, ImportError, EOFError):
        # E.g. process creation not permitted, or a worker died (BrokenProcessPool
        # is a RuntimeError).
        return None


def _can_spawn() -> bool:
    """Spawned workers re-import ``__main__``; that fails when it isn't a real file
    (e.g. a script piped through stdin)."""
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    return main_file is None or os.path.isfile(main_file)
