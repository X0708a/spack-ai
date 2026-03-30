#!/usr/bin/env python3
"""
Simulated CI execution for scheduled Spack OLE scenarios.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.config import CI_RESULTS_PATH, CI_SCHEDULED_TESTS_PATH
from shared.utils import info, load_json, warn, write_json

DEFAULT_SCHEDULED_TESTS_PATH = CI_SCHEDULED_TESTS_PATH
DEFAULT_OUTPUT_PATH = CI_RESULTS_PATH
DEFAULT_SEED = 17
FAILURE_TYPES = ("ABI", "build", "dependency conflict")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run scheduled OLE scenarios through mock or real CI.")
    parser.add_argument("scheduled_tests", nargs="?", type=Path, default=DEFAULT_SCHEDULED_TESTS_PATH)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--real-spack", action="store_true")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def _simulate_mock_result(test: dict[str, Any], deterministic: bool, seed: int) -> tuple[str, str | None]:
    score = float(test.get("score", 0.0))
    fingerprint = str(test.get("fingerprint", test.get("spec", "")))

    if deterministic:
        basis = f"{seed}:{fingerprint}:{score:.6f}"
        digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()
        roll = int(digest[:8], 16) % 100
        failure_selector = int(digest[8:10], 16)
    else:
        rng = random.Random()
        roll = rng.randint(0, 99)
        failure_selector = rng.randint(0, 255)

    failure_cutoff = min(85, 20 + int(score * 8))
    if roll < failure_cutoff:
        return "fail", FAILURE_TYPES[failure_selector % len(FAILURE_TYPES)]
    return "success", None


def _run_real_spack_check(test: dict[str, Any]) -> tuple[str, str | None, str]:
    spec = str(test.get("spec", "")).strip()
    concretize_target = re.sub(r"^spack\s+install\s+", "", spec)
    if not concretize_target:
        return "fail", "build", "empty spec"

    try:
        result = subprocess.run(
            ["spack", "spec", *shlex.split(concretize_target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except FileNotFoundError:
        return "fail", "build", "spack executable not found"
    except subprocess.TimeoutExpired:
        return "fail", "build", "spack spec timed out"

    if result.returncode == 0:
        return "success", None, result.stdout.strip()
    return "fail", "dependency conflict", result.stderr.strip() or result.stdout.strip()


def run_ci(
    scheduled_payload: dict[str, Any],
    real_spack: bool = False,
    deterministic: bool = False,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    tests = scheduled_payload.get("tests", []) if isinstance(scheduled_payload, dict) else []
    results: list[dict[str, Any]] = []
    mode = "real-spack" if real_spack else ("mock-deterministic" if deterministic else "mock")

    for test in tests:
        if not isinstance(test, dict):
            continue

        if real_spack:
            status, failure_type, detail = _run_real_spack_check(test)
        else:
            status, failure_type = _simulate_mock_result(test, deterministic=deterministic, seed=seed)
            detail = "deterministic mock" if deterministic else "mock simulation"

        results.append(
            {
                "spec": test.get("spec"),
                "primary": test.get("primary"),
                "risk_vector": test.get("risk_vector"),
                "fingerprint": test.get("fingerprint"),
                "batch_id": test.get("batch_id"),
                "score": test.get("score", 0.0),
                "status": status,
                "failure_type": failure_type,
                "detail": detail,
            }
        )

    return {
        "mode": mode,
        "result_count": len(results),
        "results": results,
    }


def main() -> None:
    args = parse_args()
    scheduled_payload = load_json(args.scheduled_tests, {"tests": []})
    ci_results = run_ci(
        scheduled_payload,
        real_spack=args.real_spack,
        deterministic=args.deterministic,
        seed=args.seed,
    )
    write_json(args.output, ci_results)
    info(f"wrote {args.output} with {ci_results['result_count']} CI result(s)")


if __name__ == "__main__":
    main()
