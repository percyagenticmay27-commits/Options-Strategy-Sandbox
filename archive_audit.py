"""Count timestamped SPX/XSP observations in read-only Robinhood archives."""
import argparse
from collections import Counter
from datetime import datetime, time
import json
from pathlib import Path
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")


def audit(folder):
    counts = Counter()
    unique = set()
    latest = {}
    input_files = 0
    in_window = set()
    for path in sorted(Path(folder).glob("*.json")):
        try:
            if path.stat().st_size > 5_000_000:
                continue
            row = json.loads(path.read_text())
            if row.get("source") != "ROBINHOOD_MCP_RESEARCH" or row.get("research_only") is not True:
                continue
            stamp = datetime.fromisoformat(row["generated_at_utc"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
            stamp = stamp.astimezone(CENTRAL)
            symbols = [s for s in ("SPX", "XSP") if row["symbols"].get(s, {}).get("contracts")]
            if not symbols:
                continue
            input_files += 1
            day = stamp.date().isoformat()
            counts[day] += len(symbols)
            keys = {(stamp.isoformat(), symbol) for symbol in symbols}
            unique.update(keys)
            if time(11) <= stamp.time() < time(11, 5):
                in_window.update(keys)
            if day not in latest or stamp.isoformat() > latest[day]["at"]:
                latest[day] = {"at": stamp.isoformat(), "file": path.name}
        except (OSError, AttributeError, KeyError, TypeError, ValueError):
            continue
    return {
        "status": "ENTRY_WINDOW_DATA_UNAVAILABLE" if not in_window else "INPUT_VERIFICATION_REQUIRED",
        "scope": "Top-level Robinhood research JSON up to 5 MB with SPX/XSP contracts",
        "files": input_files, "dates": len(counts), "raw_symbol_observations": sum(counts.values()),
        "distinct_symbol_timestamps": len(unique), "entry_window_observations": len(in_window),
        "entry_window_central": "11:00–11:05", "raw_observations_by_date": dict(counts),
        "latest_by_date": latest, "entry_window_timestamps": sorted(in_window),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_dir", type=Path)
    print(json.dumps(audit(parser.parse_args().archive_dir), sort_keys=True))
