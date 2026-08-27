#!/usr/bin/env python3
"""
watchdog_check.py — decide which editions are missing and still worth recovering.

GitHub's `schedule` trigger is best-effort. Observed on this repo: a ~30 minute
baseline delay, one run 3h34m late, and two triggers dropped outright in a
single day. A twice-daily publication cannot treat cron as reliable, so the
watchdog's job is to turn a dropped trigger into a late edition rather than a
lost one.

Two rules shape what it will and won't do:

  Due — an edition is only considered missing once its publish window has
  passed, plus a buffer for the routine delay. Checking at 4:20am PT would
  "recover" an AM edition the scheduled run is about to write.

  Still fresh — recovery is capped inside the edition's own news cycle. An AM
  edition rebuilt at 9pm would carry evening sources under a morning label,
  and a digest dated yesterday is worse than an honest gap. Past the cutoff
  the edition stays missing, deliberately.

Windows (Pacific, the only clock that names a Balm edition):
  AM  scheduled 4:15am  ->  recoverable 06:00-13:00
  PM  scheduled 2:15pm  ->  recoverable 16:00-23:59

Prints one `date:run` per line, and writes `missing=` to $GITHUB_OUTPUT.

Usage:
  python watchdog_check.py [--now 2026-08-27T10:00 America/Los_Angeles]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from dateutil import tz

DOCS_DIR = Path(__file__).parent / "docs"
PT = tz.gettz("America/Los_Angeles")

# run -> (earliest recoverable hour, latest recoverable hour, exclusive)
WINDOWS = {"am": (6, 13), "pm": (16, 24)}


def missing_editions(now_pt: datetime, docs_dir: Path = DOCS_DIR) -> list[str]:
    """Return `date:run` for each edition that is due, absent, and still fresh."""
    date_str = now_pt.strftime("%Y-%m-%d")
    out = []
    for run, (start, end) in WINDOWS.items():
        if not (start <= now_pt.hour < end):
            continue
        if not (docs_dir / f"{date_str}-{run}.html").exists():
            out.append(f"{date_str}:{run}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now", default=None,
                    help="ISO datetime to evaluate instead of now, for testing")
    a = ap.parse_args()

    now_pt = (datetime.fromisoformat(a.now).replace(tzinfo=PT) if a.now
              else datetime.now(PT))
    missing = missing_editions(now_pt)

    print(f"[watchdog] Pacific time {now_pt:%Y-%m-%d %H:%M}", file=sys.stderr)
    if missing:
        print(f"[watchdog] missing and recoverable: {', '.join(missing)}", file=sys.stderr)
    else:
        print("[watchdog] nothing due and missing", file=sys.stderr)

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"missing={','.join(missing)}\n")

    for entry in missing:
        print(entry)
    return 0


if __name__ == "__main__":
    sys.exit(main())
