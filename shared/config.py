from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

EVAL_DIR = REPO_ROOT / "eval"
CI_EXTENSION_DIR = REPO_ROOT / "ci_extension"
DOCS_DIR = REPO_ROOT / "docs"
EXAMPLES_DIR = REPO_ROOT / "examples"

EVAL_SUMMARIZE_SCRIPT = EVAL_DIR / "summarize.py"
EVAL_GENERATE_SCRIPT = EVAL_DIR / "generate_scenarios.py"
EVAL_SUMMARY_PATH = EVAL_DIR / "summary.json"
EVAL_SCENARIOS_PATH = EVAL_DIR / "scenarios.json"
EVAL_SPACK_SPEC_PATH = EVAL_DIR / "spack_spec.md"
EVAL_MARKDOWN_PATH = EVAL_SPACK_SPEC_PATH
EVAL_PROMPT_LOGIC_PATH = EVAL_DIR / "prompt_logic.md"
EVAL_ANALYSIS_CACHE_PATH = EVAL_DIR / "analysis_cache.json"

CI_MAIN_SCRIPT = CI_EXTENSION_DIR / "main.py"
CI_SCORING_SCRIPT = CI_EXTENSION_DIR / "scoring.py"
CI_SCHEDULER_SCRIPT = CI_EXTENSION_DIR / "scheduler.py"
CI_RUNNER_SCRIPT = CI_EXTENSION_DIR / "ci_runner.py"
CI_FEEDBACK_SCRIPT = CI_EXTENSION_DIR / "feedback.py"
CI_SCENARIO_SCORES_PATH = CI_EXTENSION_DIR / "scenario_scores.json"
CI_SCHEDULED_TESTS_PATH = CI_EXTENSION_DIR / "scheduled_tests.json"
CI_RESULTS_PATH = CI_EXTENSION_DIR / "ci_results.json"
CI_FAILURE_HISTORY_PATH = CI_EXTENSION_DIR / "failure_history.json"
CI_VALIDATED_SPECS_PATH = CI_EXTENSION_DIR / "validated_specs.json"
