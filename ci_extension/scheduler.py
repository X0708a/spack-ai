#!/usr/bin/env python3
"""
Schedule the highest-value OLE scenarios for the next CI run.
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
    CI_SCENARIO_SCORES_PATH,
    CI_SCHEDULED_TESTS_PATH,
    CI_VALIDATED_SPECS_PATH,
    EVAL_SCENARIOS_PATH,
)
from shared.spec_distance import dependency_names, spec_fingerprint
from shared.utils import info, load_json, write_json

DEFAULT_SCENARIOS_PATH = EVAL_SCENARIOS_PATH
DEFAULT_SCORES_PATH = CI_SCENARIO_SCORES_PATH
DEFAULT_VALIDATED_SPECS_PATH = CI_VALIDATED_SPECS_PATH
DEFAULT_OUTPUT_PATH = CI_SCHEDULED_TESTS_PATH
DEFAULT_MAX_TESTS = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule scored OLE scenarios into CI batches.")
    parser.add_argument("scenarios", nargs="?", type=Path, default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES_PATH)
    parser.add_argument("--validated-specs", type=Path, default=DEFAULT_VALIDATED_SPECS_PATH)
    parser.add_argument("--validated-spec", action="append", default=[])
    parser.add_argument("--max-tests", type=int, default=DEFAULT_MAX_TESTS)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def load_validated_specs(path: Path, cli_specs: list[str]) -> set[str]:
    payload = load_json(path, {"validated_specs": []})
    entries = payload.get("validated_specs", []) if isinstance(payload, dict) else []
    validated: set[str] = set(cli_specs)
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("spec"), str):
            validated.add(entry["spec"])
        elif isinstance(entry, str):
            validated.add(entry)
    return validated


def load_ranked_scenarios(scenarios_path: Path, scores_path: Path) -> list[dict[str, Any]]:
    scenarios_payload = load_json(scenarios_path, {"scenarios": []})
    score_payload = load_json(scores_path, {"scenarios": []})
    scenarios = scenarios_payload.get("scenarios", []) if isinstance(scenarios_payload, dict) else []
    scored = score_payload.get("scenarios", []) if isinstance(score_payload, dict) else []

    score_map: dict[str, dict[str, Any]] = {}
    for item in scored:
        if not isinstance(item, dict) or not isinstance(item.get("spec"), str):
            continue
        fingerprint = item.get("fingerprint") or spec_fingerprint(item["spec"])
        score_map[fingerprint] = item

    ranked: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict) or not isinstance(scenario.get("spec"), str):
            continue
        fingerprint = scenario.get("fingerprint") or spec_fingerprint(scenario["spec"])
        merged = dict(scenario)
        merged.update(score_map.get(fingerprint, {}))
        merged["fingerprint"] = fingerprint
        merged.setdefault("score", 0.0)
        merged["dependency_names"] = sorted(dependency_names(merged["spec"]))
        ranked.append(merged)

    ranked.sort(key=lambda item: (-float(item.get("score", 0.0)), item.get("fingerprint", "")))
    return ranked


def schedule_tests(
    ranked_scenarios: list[dict[str, Any]],
    max_tests: int,
    validated_specs: set[str],
) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    skipped_validated: list[str] = []
    skipped_duplicates: list[str] = []

    for scenario in ranked_scenarios:
        spec = scenario.get("spec", "")
        fingerprint = scenario.get("fingerprint", "")
        if spec in validated_specs:
            skipped_validated.append(spec)
            continue
        if fingerprint in seen_fingerprints:
            skipped_duplicates.append(spec)
            continue

        selected.append(scenario)
        seen_fingerprints.add(fingerprint)
        if len(selected) == max_tests:
            break

    batches: list[dict[str, Any]] = []
    for scenario in selected:
        dependency_names = set(scenario.get("dependency_names", []))
        best_batch: dict[str, Any] | None = None
        best_overlap = 0

        for batch in batches:
            overlap = len(dependency_names & batch["dependency_union"])
            if overlap > best_overlap:
                best_batch = batch
                best_overlap = overlap

        if best_batch is None or best_overlap == 0:
            batches.append(
                {
                    "batch_id": f"batch-{len(batches) + 1}",
                    "dependency_union": set(dependency_names),
                    "common_dependencies": set(dependency_names),
                    "tests": [scenario],
                }
            )
            continue

        best_batch["tests"].append(scenario)
        best_batch["dependency_union"] |= dependency_names
        best_batch["common_dependencies"] &= dependency_names

    serializable_batches = []
    flat_tests: list[dict[str, Any]] = []
    for batch in batches:
        serializable_tests = []
        for scenario in batch["tests"]:
            scheduled = dict(scenario)
            scheduled["batch_id"] = batch["batch_id"]
            serializable_tests.append(scheduled)
            flat_tests.append(scheduled)

        serializable_batches.append(
            {
                "batch_id": batch["batch_id"],
                "shared_dependencies": sorted(batch["common_dependencies"]),
                "tests": serializable_tests,
            }
        )

    return {
        "max_tests": max_tests,
        "selected_count": len(flat_tests),
        "skipped": {
            "validated_specs": skipped_validated,
            "duplicate_fingerprints": skipped_duplicates,
        },
        "batches": serializable_batches,
        "tests": flat_tests,
    }


def main() -> None:
    args = parse_args()
    ranked = load_ranked_scenarios(args.scenarios, args.scores)
    validated_specs = load_validated_specs(args.validated_specs, args.validated_spec)
    scheduled = schedule_tests(ranked, max_tests=args.max_tests, validated_specs=validated_specs)
    write_json(args.output, scheduled)
    info(f"wrote {args.output} with {scheduled['selected_count']} scheduled test(s)")


if __name__ == "__main__":
    main()
