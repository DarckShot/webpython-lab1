"""Measure calculation and cache lookup using isolated copies of the experiment."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent


def run_experiment(script):
    output = subprocess.check_output([sys.executable, script, "--compute-only"], text=True)
    return json.loads(output)


def benchmark(samples):
    timings = {"MISS": [], "HIT": []}
    with TemporaryDirectory(prefix="lab1-bench-") as directory:
        root = Path(directory)
        experiment = root / "P3"
        experiment.mkdir()
        script = experiment / "experiment.py"
        shutil.copy2(HERE / script.name, script)
        shutil.copytree(HERE / "data", experiment / "data")
        cache = root / ".cache/experiment"

        for _ in range(samples):
            for expected in timings:
                measurement = run_experiment(script)
                if measurement["cache"] != expected:
                    raise RuntimeError(f"expected {expected}, got {measurement['cache']}")
                timings[expected].append(measurement["result_ms"])
            shutil.rmtree(cache)

    report = {
        "python": sys.version.split()[0],
        "samples_per_state": samples,
        "unit": "ms",
        "states": {
            state: {"median": median(values), "min": min(values), "max": max(values), "samples": values}
            for state, values in timings.items()
        },
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be at least 1")
    benchmark(args.samples)
