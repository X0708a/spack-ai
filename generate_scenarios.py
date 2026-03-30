#!/usr/bin/env python3
"""
generate_scenarios.py - GSoC 2026 Spack-AI Diagnostic Bridge
Part 2: AI-driven OLE scenario generation
Part 3: scenario similarity scoring and deduplication
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_SUMMARY_PATH = Path("summary.json")
DEFAULT_MARKDOWN_PATH = Path("spack_spec.md")
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
DEFAULT_DEDUP_THRESHOLD = 0.18
TARGET_SCENARIO_COUNT = 3

REQUIRED_SCENARIO_KEYS = ("spec", "primary", "risk_vector", "rationale")
VERSION_RE = re.compile(r"\d+")
SPEC_TOKEN_RE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*)(@[^%\s+~^]+)?")


class AnthropicUnavailableError(RuntimeError):
    pass


SYSTEM_PROMPT = """You are a Spack package compatibility analyst specialising in
Off-Leading-Edge (OLE) regression detection.

DEFINITIONS
- OLE risk: a package version released before a breaking change in one of its
  dependencies. If the dependency is unbounded, Spack can concretize to the new
  dependency version even though that pair was never validated together.
- Middle-aged version: not the latest release listed for the primary package;
  prefer versions that are 1-3 releases behind HEAD.
- Unbounded dependency: depends_on("foo") or depends_on("foo@X:") with no
  upper ceiling.

INPUT FORMAT
- Top level:
  m.src = metadata source (informational only)
  p = package map
- Per package:
  vs = package versions, newest first
  va = guarded variant defaults, where 1 means enabled by default and 0 means disabled
  d = dependency list
- Dependency fields:
  n = dependency name
  v = version constraint (* means unconstrained, @X: means lower bound only)
  u = 1 means the dependency is unbounded and is a viable OLE vector
  w = variant guard such as +python or +gdml
  t = deptype, present only when it is not plain link

TASK
Return EXACTLY 3 risky Spack install specs. Each scenario must:
1. Use a middle-aged primary package version, never the newest listed one.
2. Pair it with the newest plausible version of an unbounded dependency.
3. Include any variant guards required by the chosen dependency.
4. Prefer link/runtime style OLE vectors over pure build-only cases when both exist.
5. Provide a one-sentence rationale tied to a plausible ABI, API, parser, or data-path break.

OUTPUT FORMAT
Return strict JSON only, no markdown fences and no extra prose:
{"scenarios":[{"spec":"spack install <...>","primary":"<pkg>@<ver>","risk_vector":"<dep>@<ver>","rationale":"<one sentence>"}]}
"""


# Simplified prompt for small local models (< 7B parameters).
# Adds WRONG/RIGHT syntax examples and explicit version-index rules
# because small models cannot reliably infer these from abstract descriptions.
SMALL_MODEL_SYSTEM_PROMPT = """You are a Spack package compatibility analyst. Find Off-Leading-Edge (OLE) risks.

OLE DEFINITION
An OLE risk is: OLD primary package + BRAND NEW dependency version.
Example: root@6.28 (old) paired with libxml2@2.13 (newest available).
This is risky because root@6.28 was never tested with libxml2@2.13.

RULES — follow exactly:
1. PRIMARY VERSION: use vs[1], vs[2], or vs[3] — never vs[0] which is newest.
2. DEPENDENCY VERSION: pick the NEWEST plausible release of an unbounded dep (u=1).
   WRONG: ^clhep@2.4.1.0  (recipe lower bound — not a new release)
   RIGHT: ^clhep@2.4.7.1  (newest version available)
3. SPACK SYNTAX: deps MUST use the ^ caret prefix.
   WRONG: spack install root@6.30.06 clhep@2.4.7.1
   RIGHT: spack install root@6.30.06 ^clhep@2.4.7.1
4. VARIANT GUARDS: if a dep has "w" field such as "+gdml", include that flag.
5. RATIONALE: one sentence naming a specific ABI, parser, or API break.

INPUT
  vs = versions newest-first. Use vs[1] or vs[2] as primary.
  d  = deps. Only use where u=1.
  v  = recipe constraint. IGNORE this — pick the NEWEST release instead.
  w  = variant guard to include in spec.

EXAMPLE
vs=["6.32.02","6.30.06","6.28.12"], dep libxml2 u=1:
  -> spack install root@6.30.06 ^libxml2@2.13.0
  primary=root@6.30.06, risk_vector=libxml2@2.13.0

OUTPUT — strict JSON only, no markdown, no prose:
{"scenarios":[{"spec":"spack install <pkg>@<ver> ^<dep>@<newest_ver>","primary":"<pkg>@<ver>","risk_vector":"<dep>@<newest_ver>","rationale":"<one sentence>"}]}
"""


def info(message: str) -> None:
    print(f"[info] {message}", file=sys.stderr)


def warn(message: str) -> None:
    print(f"[warn] {message}", file=sys.stderr)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and deduplicate OLE-risk Spack install scenarios.",
    )
    parser.add_argument(
        "summary",
        nargs="?",
        type=Path,
        default=DEFAULT_SUMMARY_PATH,
        help="Path to the compact summary JSON emitted by summarize.py.",
    )
    parser.add_argument(
        "-o",
        "--output-markdown",
        type=Path,
        default=DEFAULT_MARKDOWN_PATH,
        help="Where to write the markdown scenario report.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional path for structured scenario output.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Anthropic model name to use when the SDK and API key are available.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_DEDUP_THRESHOLD,
        help="Deduplication threshold over the weighted spec distance.",
    )
    parser.add_argument(
        "--validated-spec",
        action="append",
        default=[],
        help="Previously validated spec to exclude from the final suggestions.",
    )
    parser.add_argument(
        "--mock-only",
        action="store_true",
        help="Skip the Anthropic API and use the deterministic fallback scenarios.",
    )
    parser.add_argument(
        "--lm-studio",
        action="store_true",
        help="Use a local LM Studio server instead of the Anthropic API.",
    )
    parser.add_argument(
        "--lm-studio-model",
        default="qwen2.5-coder-3b-instruct",
        help="Model name as shown in LM Studio.",
    )
    parser.add_argument(
        "--lm-studio-url",
        default="http://localhost:1234/v1",
        help="LM Studio OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--small-model",
        action="store_true",
        help=(
            "Use the simplified prompt designed for models under 7B parameters. "
            "Adds WRONG/RIGHT examples for Spack syntax and OLE direction."
        ),
    )
    parser.add_argument(
        "--demo-dedup",
        action="store_true",
        help="Print a short deduplication demo to stderr for reviewer walkthroughs.",
    )
    return parser.parse_args()


def load_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run summarize.py first.")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("summary must be a JSON object")

    packages = data["p"] if "p" in data else data.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("summary does not contain a package map under 'p'")
    return data


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
        cleaned = cleaned.rstrip("`").strip()
    return cleaned


def changed_packages_from_summary(summary: dict[str, Any]) -> tuple[list[str], bool]:
    metadata = summary.get("m", {})
    changed = metadata.get("changed") if isinstance(metadata, dict) else None
    if not isinstance(changed, list):
        return [], False
    return [str(package) for package in changed if isinstance(package, str)], True


def build_prompt_payload(summary: dict[str, Any]) -> dict[str, Any]:
    packages = summary["p"] if "p" in summary else summary.get("packages", {})
    changed, has_delta_metadata = changed_packages_from_summary(summary)

    payload = {
        "m": dict(summary.get("m", {})),
        "p": {},
    }
    if changed:
        payload["p"] = {name: packages[name] for name in changed if name in packages}
    else:
        payload["p"] = packages
    return payload


# Missing dependency should fail fast so CI can use exit code 3 to flag
# environment setup issues instead of silently consuming fallback scenarios.
def call_anthropic(summary: dict[str, Any], model: str) -> list[dict[str, Any]]:
    try:
        import anthropic
    except ImportError as exc:
        raise AnthropicUnavailableError(
            "[error] the 'anthropic' package is not installed.\n"
            "  Install it with:  pip install anthropic\n"
            "  Then re-run:      python3 generate_scenarios.py\n"
            "  Or use mock mode: python3 generate_scenarios.py --mock-only"
        ) from exc

    try:
        client = anthropic.Anthropic()
        payload = json.dumps(build_prompt_payload(summary), separators=(",", ":"), ensure_ascii=True)
        response = client.messages.create(
            model=model,
            max_tokens=900,
            temperature=0,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Analyse this Spack summary and return 3 OLE-risk scenarios.\n\n"
                        + payload
                    ),
                }
            ],
        )
    except Exception as exc:
        warn(f"Anthropic request failed ({exc}); using deterministic fallback scenarios")
        return []

    text_blocks = [block.text for block in response.content if hasattr(block, "text")]
    raw = _strip_fences("".join(text_blocks))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f"model returned invalid JSON ({exc}); using deterministic fallback scenarios")
        return []

    scenarios = data.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios:
        warn("anthropic API returned no scenarios -- using fallback scenarios")
        return []
    return scenarios


def call_lm_studio(
    summary: dict[str, Any],
    model: str,
    url: str = "http://localhost:1234/v1",
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """
    Call a local LM Studio server via its OpenAI-compatible endpoint.
    Returns list[dict] — same type as call_anthropic — so validation and
    deduplication work unchanged.
    Pass system_prompt=SMALL_MODEL_SYSTEM_PROMPT for models under 7B.
    """
    try:
        from openai import OpenAI
    except ImportError:
        warn("'openai' package not installed; install with: pip install openai")
        return []

    client = OpenAI(base_url=url, api_key="lm-studio")
    payload = json.dumps(
        build_prompt_payload(summary), separators=(",", ":"), ensure_ascii=True
    )
    active_prompt = system_prompt if system_prompt is not None else SYSTEM_PROMPT

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=900,
            messages=[
                {"role": "system", "content": active_prompt},
                {
                    "role": "user",
                    "content": (
                        "Analyse this Spack summary and return 3 OLE-risk scenarios.\n\n"
                        + payload
                    ),
                },
            ],
        )
    except Exception as exc:
        warn(f"LM Studio request failed ({exc}); using deterministic fallback scenarios")
        return []

    if not response.choices:
        warn("LM Studio returned empty choices (check --lm-studio-url includes /v1); using fallback")
        return []
    raw = _strip_fences(response.choices[0].message.content or "")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        warn(f"LM Studio returned invalid JSON ({exc}); using deterministic fallback scenarios")
        return []

    # Some models return a bare array instead of {"scenarios":[...]}
    if isinstance(data, list):
        scenarios = data
    else:
        scenarios = data.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios:
        warn("LM Studio returned no scenarios -- using fallback scenarios")
        return []
    return scenarios


def _mock_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "spec": "spack install root@6.30.06 ^libxml2@2.13.0 ^clhep@2.4.7.1 +python +roofit",
            "risk_vector": "libxml2@2.13.0",
            "primary": "root@6.30.06",
            "source": "fallback",
            "rationale": (
                "root@6.30.06 still assumes libxml2 parser structures that changed in the 2.13 line, "
                "and the unbounded libxml2 dependency lets a fresh concretization pull that newer ABI."
            ),
        },
        {
            "spec": "spack install geant4@11.1.3 +gdml ^xerces-c@3.3.0 ^clhep@2.4.7.1",
            "risk_vector": "xerces-c@3.3.0",
            "primary": "geant4@11.1.3",
            "source": "fallback",
            "rationale": (
                "geant4@11.1.3's GDML path is sensitive to Xerces-C parser interface changes, so the "
                "lower-bound-only xerces-c constraint can admit a newer incompatible parser release."
            ),
        },
        {
            "spec": "spack install root@6.28.12 ^clhep@2.4.7.1 ^zstd@1.5.6 ~opengl",
            "risk_vector": "zstd@1.5.6",
            "primary": "root@6.28.12",
            "source": "fallback",
            "rationale": (
                "root@6.28.12 predates zstd 1.5.6 behavior changes in the compression path, and the "
                "open-ended zstd dependency allows that newer version into an otherwise older stack."
            ),
        },
        {
            "spec": "spack install geant4@11.0.4 +python ^boost@1.86.0 ^clhep@2.4.7.1",
            "risk_vector": "boost@1.86.0",
            "primary": "geant4@11.0.4",
            "source": "fallback",
            "rationale": (
                "geant4@11.0.4's Python binding layer predates newer Boost.Python compatibility shifts, "
                "so an unbounded boost dependency can pull in headers and symbols that no longer match."
            ),
        },
        {
            "spec": "spack install root@6.26.10 +opengl ^glew@2.2.0 ^clhep@2.4.7.1",
            "risk_vector": "glew@2.2.0",
            "primary": "root@6.26.10",
            "source": "fallback",
            "rationale": (
                "root@6.26.10's OpenGL path predates later GLEW integration assumptions, and the "
                "unguarded upper bound on glew can expose loader-level API drift during visualization builds."
            ),
        },
    ]


def _parse_version(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in VERSION_RE.findall(version))


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - (len(left & right) / len(union))


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


def _primary_version_distance(left: str, right: str) -> float:
    return _version_distance(left, right)


def _dep_version_distance(left: dict[str, str], right: dict[str, str]) -> float:
    common_dependencies = sorted(set(left) & set(right))
    if not common_dependencies:
        return 0.0

    distances = [_version_distance(left[name], right[name]) for name in common_dependencies]
    return sum(distances) / len(distances)


def _parse_spec(spec_str: str) -> dict[str, Any]:
    spec = re.sub(r"^spack\s+install\s+", "", spec_str.strip())
    tokens = spec.split()
    if not tokens:
        raise ValueError("empty spec")

    primary_match = SPEC_TOKEN_RE.match(tokens[0])
    if not primary_match:
        raise ValueError(f"unable to parse primary package from '{spec_str}'")

    dependencies: dict[str, str] = {}
    variants: set[str] = set()
    for token in tokens[1:]:
        if token.startswith("^"):
            dep_match = SPEC_TOKEN_RE.match(token[1:])
            if dep_match:
                dependencies[dep_match.group(1)] = dep_match.group(2) or ""
        elif token.startswith("+") or token.startswith("~"):
            variants.add(token)

    return {
        "primary": primary_match.group(1),
        "version": primary_match.group(2) or "",
        "deps": dependencies,
        "variants": variants,
    }


def spec_distance(spec_a: str, spec_b: str) -> float:
    parsed_a = _parse_spec(spec_a)
    parsed_b = _parse_spec(spec_b)

    if parsed_a["primary"] != parsed_b["primary"]:
        return 1.0

    dep_distance = _jaccard_distance(set(parsed_a["deps"]), set(parsed_b["deps"]))
    dep_version_distance = _dep_version_distance(parsed_a["deps"], parsed_b["deps"])
    variant_distance = _jaccard_distance(parsed_a["variants"], parsed_b["variants"])
    version_distance = _primary_version_distance(parsed_a["version"], parsed_b["version"])

    score = (
        (0.40 * dep_distance)
        + (0.25 * dep_version_distance)
        + (0.20 * version_distance)
        + (0.15 * variant_distance)
    )
    return min(score, 1.0)


def spec_fingerprint(spec: str) -> str:
    normalised = re.sub(r"\s+", " ", spec.strip().lower())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:12]


def validate_scenarios(raw_scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_scenarios, start=1):
        if not isinstance(raw, dict):
            warn(f"dropping scenario {index}: expected JSON object")
            continue

        missing = [key for key in REQUIRED_SCENARIO_KEYS if not raw.get(key)]
        if missing:
            warn(f"dropping scenario {index}: missing fields {', '.join(missing)}")
            continue

        scenario = {key: str(raw[key]).strip() for key in REQUIRED_SCENARIO_KEYS}
        scenario["source"] = "fallback" if raw.get("source") == "fallback" else "ai"
        if not scenario["spec"].startswith("spack install "):
            scenario["spec"] = "spack install " + scenario["spec"].lstrip()

        try:
            parsed = _parse_spec(scenario["spec"])
        except ValueError as exc:
            warn(f"dropping scenario {index}: {exc}")
            continue

        primary_pkg = scenario["primary"].split("@", 1)[0]
        if primary_pkg != parsed["primary"]:
            warn(f"dropping scenario {index}: primary package does not match spec")
            continue

        validated.append(scenario)

    return validated


def deduplicate_scenarios(
    scenarios: list[dict[str, Any]],
    validated_specs: list[str] | None = None,
    threshold: float = DEFAULT_DEDUP_THRESHOLD,
) -> list[dict[str, Any]]:
    validated_specs = list(validated_specs or [])
    accepted: list[dict[str, Any]] = []
    accepted_specs: list[str] = validated_specs[:]

    for scenario in scenarios:
        spec = scenario["spec"]
        distances = [spec_distance(spec, prior) for prior in accepted_specs]
        if all(distance >= threshold for distance in distances):
            accepted.append(scenario)
            accepted_specs.append(spec)
            continue

        closest = min(distances) if distances else 0.0
        warn(
            f"deduplicated scenario '{spec[:72]}...' "
            f"(distance={closest:.3f}, threshold={threshold:.3f})"
        )

    return accepted


def select_final_scenarios(
    primary_pool: list[dict[str, Any]],
    fallback_pool: list[dict[str, Any]],
    validated_specs: list[str],
    threshold: float,
    target_count: int = TARGET_SCENARIO_COUNT,
) -> list[dict[str, Any]]:
    accepted = deduplicate_scenarios(primary_pool, validated_specs=validated_specs, threshold=threshold)
    if len(accepted) >= target_count:
        return accepted[:target_count]

    combined_specs = validated_specs + [scenario["spec"] for scenario in accepted]
    top_up = deduplicate_scenarios(fallback_pool, validated_specs=combined_specs, threshold=threshold)
    for scenario in top_up:
        accepted.append(scenario)
        if len(accepted) == target_count:
            break
    return accepted


def write_spack_spec_md(
    scenarios: list[dict[str, Any]],
    path: Path,
    threshold: float,
    source_label: str,
) -> None:
    lines = [
        "# At-Risk Spack Specs - OLE Scenario Report",
        "",
        "Generated by `generate_scenarios.py` using the Spack-AI Diagnostic Bridge.",
        "",
        f"- Scenario source: `{source_label}`",
        f"- Dedup threshold: `{threshold:.2f}`",
        f"- Scenario count: `{len(scenarios)}`",
        "",
        "> **OLE (Off-Leading-Edge)** means a middle-aged package version is resolved",
        "> against a dependency release that the package recipe never capped with an",
        "> upper bound, so a fresh concretization can expose an untested edge.",
        "",
        "---",
        "",
    ]

    for index, scenario in enumerate(scenarios, start=1):
        lines.extend(
            [
                f"## Scenario {index}",
                "",
                f"**Primary package:** `{scenario['primary']}`  ",
                f"**Risk vector:** `{scenario['risk_vector']}`  ",
                f"**Source:** `{scenario['source']}`  ",
                f"**Fingerprint:** `{spec_fingerprint(scenario['spec'])}`",
                "",
                "### Spack install command",
                "",
                "```bash",
                scenario["spec"],
                "```",
                "",
                "### Rationale",
                "",
                scenario["rationale"],
                "",
                "---",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")
    info(f"written markdown report to {path}")


def write_scenarios_json(scenarios: list[dict[str, Any]], path: Path) -> None:
    records = []
    for scenario in scenarios:
        record = dict(scenario)
        record["fingerprint"] = spec_fingerprint(scenario["spec"])
        records.append(record)

    path.write_text(
        json.dumps({"scenarios": records}, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    info(f"written JSON report to {path}")


def demo_deduplication(scenarios: list[dict[str, Any]], threshold: float) -> None:
    if not scenarios:
        return

    print("\n-- Deduplication demo --", file=sys.stderr)
    original = scenarios[0]["spec"]
    near_duplicate = re.sub(
        r"@(\d+)\.(\d+)\.(\d+)",
        lambda match: (
            f"@{match.group(1)}.{match.group(2)}."
            f"{int(match.group(3)) + 1:0{len(match.group(3))}d}"
        ),
        original,
        count=1,
    )
    demo_pool = scenarios + [{**scenarios[0], "spec": near_duplicate}]
    reduced = deduplicate_scenarios(demo_pool, threshold=threshold)
    print(f"input={len(demo_pool)} output={len(reduced)} threshold={threshold:.2f}", file=sys.stderr)

    print("\n-- Pairwise distances --", file=sys.stderr)
    for left_index, left in enumerate(scenarios):
        for right_index, right in enumerate(scenarios):
            if right_index <= left_index:
                continue
            distance = spec_distance(left["spec"], right["spec"])
            print(
                f"scenario {left_index + 1} vs {right_index + 1}: {distance:.3f}",
                file=sys.stderr,
            )


def main() -> None:
    args = parse_args()

    try:
        summary = load_summary(args.summary)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)

    info(f"loaded summary from {args.summary}")

    changed_packages, has_delta_metadata = changed_packages_from_summary(summary)
    if has_delta_metadata and not changed_packages:
        print("No metadata changes detected — skipping AI scenario generation.")
        return

    raw_scenarios: list[dict[str, Any]]
    source_label: str
    if args.mock_only:
        raw_scenarios = _mock_scenarios()
        source_label = "deterministic-fallback"
    else:
        if args.lm_studio:
            prompt_name = "small-model" if args.small_model else "standard"
            info(
                f"requesting OLE scenarios from LM Studio "
                f"model={args.lm_studio_model} prompt={prompt_name}"
            )
            raw_scenarios = call_lm_studio(
                summary,
                model=args.lm_studio_model,
                url=args.lm_studio_url,
                system_prompt=(
                    SMALL_MODEL_SYSTEM_PROMPT if args.small_model else None
                ),
            )
            source_label = "lm-studio" if raw_scenarios else "deterministic-fallback"
        else:
            info(f"requesting OLE scenarios from model={args.model}")
            try:
                raw_scenarios = call_anthropic(summary, model=args.model)
            except AnthropicUnavailableError as exc:
                print(exc, file=sys.stderr)
                sys.exit(3)
            source_label = "anthropic" if raw_scenarios else "deterministic-fallback"
        if not raw_scenarios:
            raw_scenarios = _mock_scenarios()

    validated = validate_scenarios(raw_scenarios)
    fallback = validate_scenarios(_mock_scenarios())
    final_scenarios = select_final_scenarios(
        primary_pool=validated,
        fallback_pool=fallback,
        validated_specs=args.validated_spec,
        threshold=args.threshold,
    )

    if len(final_scenarios) != TARGET_SCENARIO_COUNT:
        print(
            "[error] unable to assemble 3 distinct scenarios after validation and deduplication",
            file=sys.stderr,
        )
        sys.exit(2)

    write_spack_spec_md(
        scenarios=final_scenarios,
        path=args.output_markdown,
        threshold=args.threshold,
        source_label=source_label,
    )
    if args.output_json:
        write_scenarios_json(final_scenarios, args.output_json)

    if args.demo_dedup:
        demo_deduplication(final_scenarios, threshold=args.threshold)

    for scenario in final_scenarios:
        print(scenario["spec"])


if __name__ == "__main__":
    main()
