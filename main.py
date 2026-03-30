#!/usr/bin/env python3
"""
End-to-end CI-aware orchestration for the Spack-AI Diagnostic Bridge pipeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import ci_runner
import feedback
import scheduler
import scoring

DEFAULT_MAX_TESTS = 5


def info(message: str) -> None:
    print(f"[info] {message}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full Spack-AI CI scheduling pipeline.")
    parser.add_argument("--max-tests", type=int, default=DEFAULT_MAX_TESTS)
    parser.add_argument("--mock-only", action="store_true")
    parser.add_argument("--force-static", action="store_true")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--seed", type=int, default=ci_runner.DEFAULT_SEED)
    parser.add_argument("--real-spack", action="store_true")
    parser.add_argument("--validated-spec", action="append", default=[])
    parser.add_argument("--summary", type=Path, default=Path("summary.json"))
    parser.add_argument("--scenarios", type=Path, default=Path("scenarios.json"))
    parser.add_argument("--scores", type=Path, default=Path("scenario_scores.json"))
    parser.add_argument("--schedule", type=Path, default=Path("scheduled_tests.json"))
    parser.add_argument("--ci-results", type=Path, default=Path("ci_results.json"))
    parser.add_argument("--failure-history", type=Path, default=Path("failure_history.json"))
    parser.add_argument("--validated-specs-cache", type=Path, default=Path("validated_specs.json"))
    return parser.parse_args()


def _run_python_step(command: list[str]) -> None:
    info("running: " + " ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _changed_packages(summary_path: Path) -> list[str]:
    if not summary_path.exists():
        return []
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    metadata = payload.get("m", {}) if isinstance(payload, dict) else {}
    changed = metadata.get("changed") if isinstance(metadata, dict) else None
    if not isinstance(changed, list):
        return []
    return [str(package) for package in changed if isinstance(package, str)]


def main() -> None:
    args = parse_args()

    summarize_cmd = [sys.executable, "summarize.py"]
    if args.force_static:
        summarize_cmd.append("--force-static")
    _run_python_step(summarize_cmd)

    changed_packages = _changed_packages(args.summary)
    if not changed_packages:
        info("No metadata deltas detected after summarization; skipping scheduling pipeline.")
        return

    generate_cmd = [sys.executable, "generate_scenarios.py", "--output-json", str(args.scenarios)]
    if args.mock_only:
        generate_cmd.append("--mock-only")
    for spec in args.validated_spec:
        generate_cmd.extend(["--validated-spec", spec])
    _run_python_step(generate_cmd)

    scored = scoring.score_scenarios(
        scenarios=scoring.load_scenarios(args.scenarios),
        packages=scoring.load_summary_packages(args.summary),
        failure_entries=scoring.load_failure_entries(args.failure_history),
        validated_specs=scoring.load_validated_specs(args.validated_specs_cache),
        previous_scores=scoring.load_json(args.scores, {"scenarios": []}).get("scenarios", []),
    )
    args.scores.write_text(json.dumps(scored, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    info(f"wrote {args.scores}")

    scheduled = scheduler.schedule_tests(
        ranked_scenarios=scheduler.load_ranked_scenarios(args.scenarios, args.scores),
        max_tests=args.max_tests,
        validated_specs=scheduler.load_validated_specs(args.validated_specs_cache, args.validated_spec),
    )
    args.schedule.write_text(json.dumps(scheduled, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    info(f"wrote {args.schedule}")

    ci_payload = ci_runner.run_ci(
        scheduled_payload=scheduled,
        real_spack=args.real_spack,
        deterministic=args.deterministic,
        seed=args.seed,
    )
    args.ci_results.write_text(json.dumps(ci_payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    info(f"wrote {args.ci_results}")

    failure_history = feedback.update_failure_history(
        ci_payload.get("results", []),
        feedback.load_json(args.failure_history, {"failures": []}),
    )
    validated_specs = feedback.update_validated_specs(
        ci_payload.get("results", []),
        feedback.load_json(args.validated_specs_cache, {"validated_specs": []}),
    )
    args.failure_history.write_text(json.dumps(failure_history, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    args.validated_specs_cache.write_text(
        json.dumps(validated_specs, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    info(f"updated {args.failure_history} and {args.validated_specs_cache}")

    boosted = feedback.apply_failure_feedback(scored, failure_history)
    args.scores.write_text(json.dumps(boosted, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    info(f"applied feedback-aware boosts to {args.scores}")


if __name__ == "__main__":
    main()
