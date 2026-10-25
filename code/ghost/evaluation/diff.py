"""Compare two ``output.csv`` files case by case.

Used to review what the agent changed relative to the deterministic baseline
on requests that have no answer key: every differing case is listed with the
steps that were added or removed, so each disagreement can be checked by hand.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from ghost.logging_setup import configure_logging, get_logger

log = get_logger()

StepKey = tuple[str, str, str, str, str, str]


def _load(path: Path) -> dict[str, tuple[str, list[StepKey]]]:
    rows: dict[str, tuple[str, list[StepKey]]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            steps = [
                (
                    s["phase"],
                    s["operation"],
                    s["system_id"],
                    s["resource_id"],
                    s["state"],
                    s["evidence_id"],
                )
                for s in json.loads(row["steps_json"])
            ]
            rows[row["case_id"]] = (row["deletion_status"], steps)
    return rows


def main(argv: list[str] | None = None) -> int:
    """Print the rows that differ between two output files."""
    parser = argparse.ArgumentParser(prog="ghost-diff")
    parser.add_argument("left", type=Path, help="reference output.csv (e.g. baseline)")
    parser.add_argument("right", type=Path, help="candidate output.csv (e.g. agent)")
    args = parser.parse_args(argv)
    configure_logging(False)
    left, right = _load(args.left), _load(args.right)
    shared = sorted(set(left) & set(right), key=lambda c: int(c.split("_")[-1]))
    changed = status_changed = 0
    for case_id in shared:
        (ls, lsteps), (rs, rsteps) = left[case_id], right[case_id]
        if ls == rs and lsteps == rsteps:
            continue
        changed += 1
        status_changed += ls != rs
        flag = f"  STATUS {ls} -> {rs}" if ls != rs else ""
        log.info(f"-- {case_id}{flag}")
        for t in sorted(set(lsteps) - set(rsteps)):
            log.info(
                f"   removed  {t[0][:5]} {t[1]:<26} {t[2]:<20} {t[3]:<18} {t[4]:<10} ev={t[5]}"
            )
        for t in sorted(set(rsteps) - set(lsteps)):
            log.info(
                f"   added    {t[0][:5]} {t[1]:<26} {t[2]:<20} {t[3]:<18} {t[4]:<10} ev={t[5]}"
            )
        if set(lsteps) == set(rsteps):
            log.info("   (same steps, different order)")
    log.info(
        f"{len(shared)} shared cases; {changed} differ ({status_changed} with a status change);"
        f" {len(shared) - changed} identical"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
