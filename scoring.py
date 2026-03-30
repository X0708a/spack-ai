#!/usr/bin/env python3
"""
Scenario scoring for the Spack-AI Diagnostic Bridge pipeline.

Scores combine recipe risk, package age, historical rarity, and nearby failure
history so CI can prioritize the most informative OLE scenarios first.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from generate_scenarios import spec_distance, spec_fingerprint

DEFAULT_SCENARIOS_PATH = Path("scenarios.json")
DEFAULT_SUMMARY_PATH = Path("summary.json")
DEFAULT_OUTPUT_PATH = Path("scenario_scores.json")
DEFAULT_FAILURE_HISTORY_PATH = Path("failure_history.json")
DEFAULT_VALIDATED_SPECS_PATH = Path("validated_specs.json")

DEFAULT_WEIGHTS = {
    "unbounded_dependency_count": 3.0,
    "version_distance_from_latest": 2.0,
    "rarity_score": 1.25,
    "failure_history_weight": 2.5,
}

VERSION_RE = re.compile(r"\d+")
SPEC_TOKEN_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*)(@[^%\s+~^]+)?")


def info(message: str) -> None:
    print(f"[info] {message}", file=sys.stderr)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


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


def _parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in VERSION_RE.findall(version))


def _version_distance(left: str, right: str) -> float:
    left_tuple = _parse_version(left)
    right_tuple = _parse_version(right)
    if not left_tuple and not right_tuple:
        return 0.0
    if not left_tuple or not right_tuple:
        return 1.0
    if left_tuple == right_tuple:
        return 0.0

    max_len = max(len(left_tuple), len(right_tuple), 3)
    padded_left = left_tuple + (0,) * (max_len - len(left_tuple))
    padded_right = right_tuple + (0,) * (max_len - len(right_tuple))

    major_gap = min(abs(padded_left[0] - padded_right[0]), 1)
    minor_gap = min(abs(padded_left[1] - padded_right[1]), 5) / 5.0
    patch_gap = min(abs(padded_left[2] - padded_right[2]), 10) / 10.0
    return min(1.0, (0.6 * major_gap) + (0.25 * minor_gap) + (0.15 * patch_gap))


def _parse_spec(spec: str) -> dict[str, Any]:
    stripped = re.sub(r"^spack\s+install\s+", "", spec.strip())
    tokens = stripped.split()
    primary = {"name": "", "version": ""}
    dependencies: dict[str, str] = {}

    if tokens:
        match = SPEC_TOKEN_RE.match(tokens[0])
        if match:
            primary = {"name": match.group(1), "version": match.group(2) or ""}

    for token in tokens[1:]:
        if not token.startswith("^"):
            continue
        match = SPEC_TOKEN_RE.match(token[1:])
        if match:
            dependencies[match.group(1)] = match.group(2) or ""

    return {"primary": primary, "dependencies": dependencies}


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
    return _version_distance(primary_version, latest)


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

        parsed = _parse_spec(spec)
        scored.append(
            {
                **scenario,
                "fingerprint": fingerprint,
                "score": round(score, 6),
                "components": components,
                "dependency_names": sorted(parsed["dependencies"]),
                "primary_package": parsed["primary"]["name"],
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
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    info(f"wrote {args.output} with {result['scenario_count']} scored scenario(s)")


if __name__ == "__main__":
    main()
