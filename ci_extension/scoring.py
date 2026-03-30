#!/usr/bin/env python3
"""
Scenario scoring for the Spack-AI Diagnostic Bridge pipeline.

Scores combine recipe risk, package age, historical rarity, and nearby failure
history so CI can prioritize the most informative OLE scenarios first.
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
    CI_SCENARIO_SCORES_PATH,
    CI_VALIDATED_SPECS_PATH,
    EVAL_SCENARIOS_PATH,
    EVAL_SUMMARY_PATH,
)
from shared.spec_distance import parse_spec, spec_distance, spec_fingerprint, version_distance
from shared.utils import info, load_json, write_json

DEFAULT_SCENARIOS_PATH = EVAL_SCENARIOS_PATH
DEFAULT_SUMMARY_PATH = EVAL_SUMMARY_PATH
DEFAULT_OUTPUT_PATH = CI_SCENARIO_SCORES_PATH
DEFAULT_FAILURE_HISTORY_PATH = CI_FAILURE_HISTORY_PATH
DEFAULT_VALIDATED_SPECS_PATH = CI_VALIDATED_SPECS_PATH

DEFAULT_WEIGHTS = {
    "unbounded_dependency_count": 3.0,
    "version_distance_from_latest": 2.0,
    "rarity_score": 1.25,
    "failure_history_weight": 2.5,
}

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score generated OLE scenarios for CI scheduling.")
    parser.add_argument("scenarios", nargs="?", type=Path, default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY_PATH)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--failure-history", type=Path, default=DEFAULT_FAILURE_HISTORY_PATH)
    parser.add_argument("--validated-specs", type=Path, default=DEFAULT_VALIDATED_SPECS_PATH)
    return parser.parse_args()


def load_scenarios(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, {"scenarios": []})
    scenarios = payload.get("scenarios", []) if isinstance(payload, dict) else []
    return [scenario for scenario in scenarios if isinstance(scenario, dict)]


def load_summary_packages(path: Path) -> dict[str, dict[str, Any]]:
    payload = load_json(path, {})
    packages = payload.get("p") or payload.get("packages") or {}
    return packages if isinstance(packages, dict) else {}


def load_failure_entries(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, {"failures": []})
    failures = payload.get("failures", []) if isinstance(payload, dict) else []
    return [failure for failure in failures if isinstance(failure, dict)]


def load_validated_specs(path: Path) -> list[str]:
    payload = load_json(path, {"validated_specs": []})
    entries = payload.get("validated_specs", []) if isinstance(payload, dict) else []
    specs: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("spec"), str):
            specs.append(entry["spec"])
        elif isinstance(entry, str):
            specs.append(entry)
    return specs


def _unbounded_dependency_count(scenario: dict[str, Any], packages: dict[str, dict[str, Any]]) -> int:
    primary_name = scenario.get("primary", "").split("@", 1)[0]
    package = packages.get(primary_name, {})
    dependencies = package.get("d", []) if isinstance(package, dict) else []
    return sum(1 for dependency in dependencies if isinstance(dependency, dict) and dependency.get("u") == 1)


def _version_distance_from_latest(scenario: dict[str, Any], packages: dict[str, dict[str, Any]]) -> float:
    primary = scenario.get("primary", "")
    primary_name, _, primary_version = primary.partition("@")
    package = packages.get(primary_name, {})
    versions = package.get("vs", []) if isinstance(package, dict) else []
    latest = versions[0] if versions else ""
    return version_distance(primary_version, latest)


def _rarity_score(spec: str, historical_specs: list[str]) -> float:
    if not historical_specs:
        return 1.0
    return min(spec_distance(spec, previous) for previous in historical_specs)


def _failure_history_weight(spec: str, failure_entries: list[dict[str, Any]]) -> float:
    if not failure_entries:
        return 0.0

    influences: list[float] = []
    for failure in failure_entries:
        failure_spec = failure.get("spec")
        if not isinstance(failure_spec, str):
            continue
        similarity = 1.0 - spec_distance(spec, failure_spec)
        fail_count = max(1, int(failure.get("fail_count", 1)))
        influences.append(similarity * min(fail_count, 5) / 5.0)
    return max(influences, default=0.0)


def score_scenarios(
    scenarios: list[dict[str, Any]],
    packages: dict[str, dict[str, Any]],
    failure_entries: list[dict[str, Any]],
    validated_specs: list[str],
    previous_scores: list[dict[str, Any]] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    historical_specs = list(validated_specs)
    for record in previous_scores or []:
        if isinstance(record, dict) and isinstance(record.get("spec"), str):
            historical_specs.append(record["spec"])

    scored: list[dict[str, Any]] = []
    for scenario in scenarios:
        spec = str(scenario.get("spec", "")).strip()
        if not spec:
            continue

        fingerprint = spec_fingerprint(spec)
        components = {
            "unbounded_dependency_count": _unbounded_dependency_count(scenario, packages),
            "version_distance_from_latest": _version_distance_from_latest(scenario, packages),
            "rarity_score": _rarity_score(spec, historical_specs),
            "failure_history_weight": _failure_history_weight(spec, failure_entries),
        }

        score = (
            (weights["unbounded_dependency_count"] * components["unbounded_dependency_count"])
            + (weights["version_distance_from_latest"] * components["version_distance_from_latest"])
            + (weights["rarity_score"] * components["rarity_score"])
            + (weights["failure_history_weight"] * components["failure_history_weight"])
        )

        parsed = parse_spec(spec)
        scored.append(
            {
                **scenario,
                "fingerprint": fingerprint,
                "score": round(score, 6),
                "components": components,
                "dependency_names": sorted(parsed["deps"]),
                "primary_package": parsed["primary"],
            }
        )

    scored.sort(key=lambda item: (-float(item.get("score", 0.0)), item.get("fingerprint", "")))
    return {
        "weights": weights,
        "scenario_count": len(scored),
        "scenarios": scored,
    }


def main() -> None:
    args = parse_args()
    scenarios = load_scenarios(args.scenarios)
    packages = load_summary_packages(args.summary)
    failure_entries = load_failure_entries(args.failure_history)
    validated_specs = load_validated_specs(args.validated_specs)
    previous_payload = load_json(args.output, {"scenarios": []})
    previous_scores = previous_payload.get("scenarios", []) if isinstance(previous_payload, dict) else []

    result = score_scenarios(
        scenarios=scenarios,
        packages=packages,
        failure_entries=failure_entries,
        validated_specs=validated_specs,
        previous_scores=previous_scores,
    )
    write_json(args.output, result)
    info(f"wrote {args.output} with {result['scenario_count']} scored scenario(s)")


if __name__ == "__main__":
    main()
