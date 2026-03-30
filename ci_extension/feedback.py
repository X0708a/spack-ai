#!/usr/bin/env python3
"""
Feedback loop for CI results.

Updates failure history, validated specs, and boosts future scheduling scores for
configurations that are close to prior failures.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.config import (
    CI_FAILURE_HISTORY_PATH,
    CI_RESULTS_PATH,
    CI_SCENARIO_SCORES_PATH,
    CI_VALIDATED_SPECS_PATH,
)
from shared.spec_distance import spec_distance, spec_fingerprint
from shared.utils import info, load_json, write_json

DEFAULT_CI_RESULTS_PATH = CI_RESULTS_PATH
DEFAULT_FAILURE_HISTORY_PATH = CI_FAILURE_HISTORY_PATH
DEFAULT_VALIDATED_SPECS_PATH = CI_VALIDATED_SPECS_PATH
DEFAULT_SCENARIO_SCORES_PATH = CI_SCENARIO_SCORES_PATH

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update failure history and validated caches from CI results.")
    parser.add_argument("ci_results", nargs="?", type=Path, default=DEFAULT_CI_RESULTS_PATH)
    parser.add_argument("--failure-history", type=Path, default=DEFAULT_FAILURE_HISTORY_PATH)
    parser.add_argument("--validated-specs", type=Path, default=DEFAULT_VALIDATED_SPECS_PATH)
    parser.add_argument("--scenario-scores", type=Path, default=DEFAULT_SCENARIO_SCORES_PATH)
    return parser.parse_args()


def update_failure_history(ci_results: list[dict[str, Any]], history_payload: dict[str, Any]) -> dict[str, Any]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in history_payload.get("failures", []):
        if isinstance(entry, dict) and isinstance(entry.get("fingerprint"), str):
            indexed[entry["fingerprint"]] = dict(entry)

    for result in ci_results:
        if result.get("status") != "fail":
            continue
        fingerprint = str(result.get("fingerprint") or spec_fingerprint(str(result.get("spec", ""))))
        record = indexed.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "spec": result.get("spec"),
                "failure_type": result.get("failure_type"),
                "fail_count": 0,
            },
        )
        record["spec"] = result.get("spec")
        record["failure_type"] = result.get("failure_type")
        record["fail_count"] = int(record.get("fail_count", 0)) + 1

    failures = sorted(indexed.values(), key=lambda item: (-int(item.get("fail_count", 0)), item.get("fingerprint", "")))
    return {"failures": failures}


def update_validated_specs(ci_results: list[dict[str, Any]], validated_payload: dict[str, Any]) -> dict[str, Any]:
    indexed: dict[str, dict[str, Any]] = {}
    for entry in validated_payload.get("validated_specs", []):
        if isinstance(entry, dict) and isinstance(entry.get("fingerprint"), str):
            indexed[entry["fingerprint"]] = dict(entry)

    for result in ci_results:
        if result.get("status") != "success":
            continue
        fingerprint = str(result.get("fingerprint") or spec_fingerprint(str(result.get("spec", ""))))
        indexed[fingerprint] = {
            "fingerprint": fingerprint,
            "spec": result.get("spec"),
            "status": "success",
        }

    validated_specs = sorted(indexed.values(), key=lambda item: item.get("fingerprint", ""))
    return {"validated_specs": validated_specs}


def apply_failure_feedback(scores_payload: dict[str, Any], failure_history: dict[str, Any]) -> dict[str, Any]:
    failures = [entry for entry in failure_history.get("failures", []) if isinstance(entry, dict)]
    scenarios = [entry for entry in scores_payload.get("scenarios", []) if isinstance(entry, dict)]

    for scenario in scenarios:
        spec = scenario.get("spec")
        if not isinstance(spec, str):
            continue

        boosts = []
        for failure in failures:
            failure_spec = failure.get("spec")
            if not isinstance(failure_spec, str):
                continue
            similarity = 1.0 - spec_distance(spec, failure_spec)
            fail_count = min(int(failure.get("fail_count", 1)), 5)
            boosts.append(similarity * fail_count * 0.5)

        feedback_boost = round(max(boosts, default=0.0), 6)
        scenario["feedback_boost"] = feedback_boost
        scenario["score"] = round(float(scenario.get("score", 0.0)) + feedback_boost, 6)

    scenarios.sort(key=lambda item: (-float(item.get("score", 0.0)), item.get("fingerprint", "")))
    return {
        **scores_payload,
        "scenarios": scenarios,
    }


def main() -> None:
    args = parse_args()
    ci_payload = load_json(args.ci_results, {"results": []})
    ci_results = ci_payload.get("results", []) if isinstance(ci_payload, dict) else []

    failure_history = update_failure_history(ci_results, load_json(args.failure_history, {"failures": []}))
    validated_specs = update_validated_specs(ci_results, load_json(args.validated_specs, {"validated_specs": []}))

    write_json(args.failure_history, failure_history)
    write_json(args.validated_specs, validated_specs)
    info(f"updated {args.failure_history} and {args.validated_specs}")

    if args.scenario_scores.exists():
        scores_payload = load_json(args.scenario_scores, {"scenarios": []})
        boosted = apply_failure_feedback(scores_payload, failure_history)
        write_json(args.scenario_scores, boosted)
        info(f"applied failure-aware score boosts to {args.scenario_scores}")


if __name__ == "__main__":
    main()
