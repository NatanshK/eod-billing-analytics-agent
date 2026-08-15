"""Seed the store with the sample clinic-days.

Runs only when the store is empty, so it populates a fresh instance — including
the ephemeral filesystem of a free-tier host — without overwriting data someone
has ingested.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

from .core.parsing import extract_rows, load_payload, parse_billing_log
from .storage import repository as repo

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).parent.parent / "seed_data"


def seeding_enabled() -> bool:
    """Off in tests, which assert against exactly the data they ingested."""
    return os.environ.get("SEED_ON_STARTUP", "true").lower() not in {"false", "0", "no"}


def seed_if_empty(directory: Path | None = None) -> int:
    """Ingest every sample day, but only into an empty store."""
    if not seeding_enabled() or repo.list_days():
        return 0
    return load_seed_files(directory)


def load_seed_files(directory: Path | None = None) -> int:
    """Ingest every JSON file in the seed directory. Returns days loaded."""
    seed_dir = directory or SEED_DIR
    if not seed_dir.is_dir():
        return 0

    loaded = 0
    for path in sorted(seed_dir.glob("*.json")):
        try:
            rows = extract_rows(load_payload(path.read_text()))
            parsed = parse_billing_log(rows)

            # Same policy as the ingest endpoint: quarantine bad rows rather
            # than dropping the whole file.
            rejected = [e.to_dict() for e in parsed.errors]
            parsed.errors = []

            if not parsed.visits:
                # A day with no visits carries no clinic or date, so take them
                # from the filename convention: billing_log_YYYY-MM-DD.json.
                identity = _identity_from_filename(path)
                if identity is None:
                    log.warning("skipping %s: empty and not attributable", path.name)
                    continue
                parsed.clinic_id, parsed.business_date = identity

            repo.save_day(parsed, rows, rejected=rejected, rows_received=parsed.rows_seen)
            loaded += 1
        except Exception:  # pragma: no cover - never block startup on seed data
            log.exception("could not load seed file %s", path.name)

    if loaded:
        log.info("seeded %d clinic-day(s) from %s", loaded, seed_dir)
    return loaded


#: Which clinic an empty seed file belongs to. Only used when the file has no
#: rows to say; a real deployment would pass this in at upload time.
SEED_CLINIC_ID = os.environ.get("SEED_CLINIC_ID", "CLN-KNP-014")


def _identity_from_filename(path: Path) -> tuple[str, date] | None:
    """Recover (clinic_id, date) for an empty day from ``*_YYYY-MM-DD.json``."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if not match:
        return None
    try:
        return SEED_CLINIC_ID, date.fromisoformat(match.group(1))
    except ValueError:  # pragma: no cover - regex already constrains the shape
        return None
