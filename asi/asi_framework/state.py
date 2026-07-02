"""
Shared checkpoint/serialization primitives, used by every search strategy's
own resumable-state dataclass (see search.py's GreedySearchState and
spea2.py's Spea2SearchState).

Each strategy owns its own state shape (the greedy search's Pareto front +
freeze bookkeeping look nothing like SPEA2's populations/archives), but the
mechanics of "turn a DesignPoint into JSON and back", "write the state file
without corrupting it if we get killed mid-write", and "figure out which
strategy a saved state file belongs to" are identical across strategies.
Centralizing them here means a future strategy only has to write its own
to_dict/from_dict for its strategy-specific fields.
"""
import dataclasses
import json
import shutil
from pathlib import Path

from .models import DesignPoint


def state_path(outputdir: Path) -> Path:
    return outputdir / "search_state.json"


def point_to_dict(p: DesignPoint) -> dict:
    d = dataclasses.asdict(p)
    d["modified_params"] = sorted(d["modified_params"])
    d["output_path"] = str(d["output_path"]) if d["output_path"] else None
    return d


def point_from_dict(d: dict) -> DesignPoint:
    d = dict(d)
    d["modified_params"] = set(d["modified_params"])
    d["output_path"] = Path(d["output_path"]) if d["output_path"] else None
    return DesignPoint(**d)


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def read_raw_state(outputdir: Path) -> dict | None:
    """Read the raw saved-state JSON for this outputdir, or None if none exists."""
    path = state_path(outputdir)
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def rng_state_to_json(rng) -> list:
    """random.Random.getstate() -> JSON-safe nested list, so a resumed run
    continues the exact same pseudo-random sequence rather than reseeding."""
    version, internal_state, gauss_next = rng.getstate()
    return [version, list(internal_state), gauss_next]


def rng_state_from_json(data: list) -> tuple:
    version, internal_state, gauss_next = data
    return (version, tuple(internal_state), gauss_next)


def cleanup_dirs(dirs: set[Path]) -> int:
    """Delete a set of Sniper output directories that are no longer referenced
    by any live design point. Shared by every strategy so that a search that
    evaluates far more candidates than it ultimately needs (e.g. an
    evolutionary search's discarded population members) doesn't leave
    hundreds of stale multi-megabyte output directories behind."""
    count = 0
    for d in dirs:
        if d and d.exists():
            shutil.rmtree(d, ignore_errors=True)
            count += 1
    return count
