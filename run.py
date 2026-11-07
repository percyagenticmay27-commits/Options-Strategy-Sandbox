"""Run the isolated replay and publish an immutable paper ledger under runs/."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

from replay import compare, VERSION

ROOT = Path(__file__).resolve().parent


def run(dataset_path):
    raw = Path(dataset_path).read_bytes()
    report = compare(json.loads(raw))
    dataset_hash = hashlib.sha256(raw).hexdigest()
    engine_hash = hashlib.sha256((ROOT / "replay.py").read_bytes()).hexdigest()
    report.update(dataset_sha256=dataset_hash, engine_sha256=engine_hash)
    if report["dataset_kind"] == "SYNTHETIC_FIXTURE":
        report["evidence_status"] = "SYNTHETIC_VALIDATION"
    elif any(profile["metrics"]["scored"] == 0 for profile in report["profiles"].values()):
        report["evidence_status"] = "INSUFFICIENT_HISTORICAL_EVIDENCE"
    else:
        report["evidence_status"] = "HISTORICAL_RESEARCH_SAMPLE"
    run_id = hashlib.sha256(f"{VERSION}:{dataset_hash}:{engine_hash}".encode()).hexdigest()
    report["run_id"] = run_id
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    out = ROOT / "runs"
    if out.is_symlink():
        raise ValueError("runs must be a real directory inside the sandbox")
    out.mkdir(exist_ok=True)
    target = out / f"{run_id}.json"
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(temp, target)
            except FileExistsError:
                if target.is_symlink() or target.read_text(encoding="utf-8") != rendered:
                    raise ValueError("Existing ledger differs for identical replay inputs")
        finally:
            temp.unlink()
    return target, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    try:
        target, report = run(args.dataset)
    except (AttributeError, KeyError, TypeError, ValueError, OSError, ArithmeticError) as error:
        parser.exit(2, f"Replay rejected: {error}\n")
    print(json.dumps({
        "ledger": str(target),
        "status": report["evidence_status"],
        "coverage": report["coverage"],
        "metrics": {name: value["metrics"] for name, value in report["profiles"].items()},
        "shadow_1030_metrics": {
            name: value["metrics"] for name, value in report["cutoff_observation"]["profiles"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
