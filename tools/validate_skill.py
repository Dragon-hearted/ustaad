#!/usr/bin/env python3
"""Validate an Ustaad skill file.

Runs two layers of checks:
  1. JSON Schema (schemas/skill.schema.json, draft 2020-12).
  2. Cross-reference invariants the schema cannot express: unique IDs,
     dependencies that exist and form a DAG, order mistakes consistent with
     the graph, assets that exist and have the right type and shape, every
     localized string covering every available language, ordered thresholds,
     and source spans that fit the recording.

Usage:
    python tools/validate_skill.py examples/masala-chai/skill.json
    python tools/validate_skill.py path/to/skill.json --schema path/to/schema.json

Exit code 0 when valid (warnings may still print), 1 when invalid.
Requires: pip install "jsonschema>=4.18"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import best_match
except ImportError:  # pragma: no cover - environment guard
    sys.exit('Missing dependency: pip install "jsonschema>=4.18"')

DEFAULT_SCHEMA = Path(__file__).resolve().parent.parent / "schemas" / "skill.schema.json"
EMBEDDING_MIME = "application/x-ustaad-embedding"


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where}: {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where}: {message}")


def json_path(parts) -> str:
    path = "$"
    for part in parts:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


def friendly_message(err) -> tuple[str, str]:
    """Return (location, message) without dumping whole instances into the output."""
    if err.validator in ("oneOf", "anyOf") and err.context:
        inner = best_match(err.context)
        return json_path(inner.absolute_path), f"{inner.message} (no allowed shape matched)"
    if err.validator == "not" and isinstance(err.validator_value, dict) and set(err.validator_value) == {"required"}:
        fields = ", ".join(f"'{name}'" for name in err.validator_value["required"])
        return json_path(err.absolute_path), f"{fields} must not be set here"
    return json_path(err.absolute_path), err.message


def check_schema(skill: dict, schema: dict, report: Report) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for err in sorted(validator.iter_errors(skill), key=lambda e: list(e.absolute_path)):
        report.error(*friendly_message(err))


def find_cycle(deps: dict[str, list[str]]) -> list[str] | None:
    """Return one dependency cycle as a path (first node repeated at the end), or None."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in deps}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        colour[node] = GREY
        stack.append(node)
        for parent in deps.get(node, []):
            if parent not in colour:
                continue
            if colour[parent] == GREY:
                return stack[stack.index(parent):] + [parent]
            if colour[parent] == WHITE:
                found = visit(parent)
                if found:
                    return found
        stack.pop()
        colour[node] = BLACK
        return None

    for node in deps:
        if colour[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def localized_fields(skill: dict):
    """Yield (location, localized-text dict) for every localized string in the skill."""
    yield "$.title", skill.get("title")
    if "summary" in skill:
        yield "$.summary", skill["summary"]
    for i, material in enumerate(skill.get("materials", [])):
        yield f"$.materials[{i}].name", material.get("name")
        if "quantity" in material:
            yield f"$.materials[{i}].quantity", material["quantity"]
    for i, step in enumerate(skill.get("steps", [])):
        base = f"$.steps[{i}]"
        yield f"{base}.title", step.get("title")
        yield f"{base}.instruction.text", step.get("instruction", {}).get("text")
        for j, mistake in enumerate(step.get("mistakes", [])):
            yield f"{base}.mistakes[{j}].message.text", mistake.get("message", {}).get("text")
        for j, hint in enumerate(step.get("hints", [])):
            yield f"{base}.hints[{j}].message.text", hint.get("message", {}).get("text")


def ancestors_of(step_id: str, deps: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(deps.get(step_id, []))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(deps.get(current, []))
    return seen


def check_invariants(skill: dict, report: Report) -> None:
    steps = skill["steps"]
    assets = skill["assets"]
    available = skill["languages"]["available"]
    perception = skill["perception"]
    privacy = skill["privacy"]

    # Languages
    if skill["languages"]["authored"] not in available:
        report.error("$.languages.authored", "must be one of languages.available")
    for where, text in localized_fields(skill):
        missing = [lang for lang in available if not text or lang not in text]
        if missing:
            report.error(where, f"missing language(s) {missing}")

    # Unique IDs
    step_ids = [s["id"] for s in steps]
    for dup in sorted({x for x in step_ids if step_ids.count(x) > 1}):
        report.error("$.steps", f"duplicate step id '{dup}'")
    material_ids = [m["id"] for m in skill["materials"]]
    for dup in sorted({x for x in material_ids if material_ids.count(x) > 1}):
        report.error("$.materials", f"duplicate material id '{dup}'")
    mistake_ids = [m["id"] for s in steps for m in s.get("mistakes", [])]
    for dup in sorted({x for x in mistake_ids if mistake_ids.count(x) > 1}):
        report.error("$.steps[*].mistakes", f"duplicate mistake id '{dup}'")

    known_steps = set(step_ids)
    known_materials = set(material_ids)
    deps = {s["id"]: list(s.get("dependsOn", [])) for s in steps}

    # Dependencies exist, no self-dependency
    for i, step in enumerate(steps):
        for dep in step.get("dependsOn", []):
            if dep == step["id"]:
                report.error(f"$.steps[{i}].dependsOn", "a step cannot depend on itself")
            elif dep not in known_steps:
                report.error(f"$.steps[{i}].dependsOn", f"unknown step '{dep}'")

    # Acyclic
    cycle = find_cycle({sid: [p for p in parents if p != sid] for sid, parents in deps.items()})
    if cycle:
        # dependsOn points from a step to its prerequisites, so read the path backwards.
        report.error("$.steps", "dependency cycle: " + " → ".join(reversed(cycle)))

    # Thresholds and work zone
    t = perception["thresholds"]
    if not (t["low"] < t["high"] < t["autoAccept"]):
        report.error("$.perception.thresholds", "must satisfy low < high < autoAccept")
    zone = perception.get("workZone")
    if zone and zone["pitchMinDeg"] >= zone["pitchMaxDeg"]:
        report.error("$.perception.workZone", "pitchMinDeg must be less than pitchMaxDeg")

    # Assets referenced from anywhere
    def require_asset(where: str, asset_id: str | None, *, embedding: bool = False) -> None:
        if asset_id is None:
            return
        asset = assets.get(asset_id)
        if asset is None:
            report.error(where, f"unknown asset '{asset_id}'")
            return
        if embedding:
            if asset["mime"] != EMBEDDING_MIME:
                report.error(where, f"asset '{asset_id}' must be an embedding, got {asset['mime']}")
            elif asset["embedding"]["dim"] != perception["embeddingModel"]["dim"]:
                report.error(
                    where,
                    f"asset '{asset_id}' has dim {asset['embedding']['dim']}, "
                    f"perception.embeddingModel.dim is {perception['embeddingModel']['dim']}",
                )

    used_assets: set[str] = set()
    for i, material in enumerate(skill["materials"]):
        require_asset(f"$.materials[{i}].reference", material.get("reference"))
        used_assets.add(material.get("reference"))

    recording_ms = skill["provenance"].get("sourceRecording", {}).get("durationMs")
    previous_end = 0
    for i, step in enumerate(steps):
        base = f"$.steps[{i}]"
        clip = step["instruction"].get("clip")
        require_asset(f"{base}.instruction.clip", clip)
        used_assets.add(clip)

        signal_types = {s["type"] for s in step["completion"]["signals"]}
        refs = step.get("references")
        if "visual" in signal_types:
            if not refs:
                report.error(base, "visual steps need references.after")
            if "calibration" not in step:
                report.error(base, "visual steps need calibration")
        if refs:
            for phase in ("before", "during", "after"):
                for asset_id in refs.get(phase, []):
                    require_asset(f"{base}.references.{phase}", asset_id, embedding=True)
                    used_assets.add(asset_id)

        for j, mistake in enumerate(step.get("mistakes", [])):
            where = f"{base}.mistakes[{j}]"
            require_asset(f"{where}.message.clip", mistake["message"].get("clip"))
            used_assets.add(mistake["message"].get("clip"))
            if mistake["kind"] == "order":
                ancestors = ancestors_of(step["id"], deps)
                for req in mistake.get("requires", []):
                    if req == step["id"]:
                        report.error(f"{where}.requires", "a step cannot require itself")
                    elif req not in known_steps:
                        report.error(f"{where}.requires", f"unknown step '{req}'")
                    elif req not in ancestors:
                        report.error(
                            f"{where}.requires",
                            f"'{req}' is not an ancestor of '{step['id']}' in dependsOn, "
                            "so the graph and the mistake disagree",
                        )

        for j, hint in enumerate(step.get("hints", [])):
            require_asset(f"{base}.hints[{j}].message.clip", hint["message"].get("clip"))
            used_assets.add(hint["message"].get("clip"))

        for material in step.get("materials", []):
            if material not in known_materials:
                report.error(f"{base}.materials", f"unknown material '{material}'")

        duration = step["expectedDurationSec"]
        if duration["p50"] > duration["p90"]:
            report.error(f"{base}.expectedDurationSec", "p50 must not exceed p90")

        span = step["sourceSpanMs"]
        if span["startMs"] >= span["endMs"]:
            report.error(f"{base}.sourceSpanMs", "startMs must be before endMs")
        if span["startMs"] < previous_end:
            report.error(
                f"{base}.sourceSpanMs",
                "overlaps the previous step; steps are listed in demonstration order",
            )
        if recording_ms is not None and span["endMs"] > recording_ms:
            report.error(f"{base}.sourceSpanMs", "ends after the source recording")
        previous_end = max(previous_end, span["endMs"])

    for sid in skill["provenance"].get("selfConsistency", {}).get("weakSteps", []):
        if sid not in known_steps:
            report.error("$.provenance.selfConsistency.weakSteps", f"unknown step '{sid}'")

    # Privacy: no keyframe images unless the author allowed them
    if not privacy["keepReferenceImages"]:
        for asset_id, asset in assets.items():
            if asset["mime"].startswith("image/"):
                report.error(
                    f"$.assets.{asset_id}",
                    "image assets are not allowed when privacy.keepReferenceImages is false",
                )

    # Asset hygiene
    paths = [a["path"] for a in assets.values()]
    for dup in sorted({p for p in paths if paths.count(p) > 1}):
        report.error("$.assets", f"duplicate asset path '{dup}'")
    for asset_id in sorted(set(assets) - used_assets):
        report.warn(f"$.assets.{asset_id}", "asset is never referenced")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an Ustaad skill file.")
    parser.add_argument("skill", type=Path, help="path to skill.json")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="path to skill.schema.json")
    args = parser.parse_args(argv)

    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    skill = json.loads(args.skill.read_text(encoding="utf-8"))

    report = Report()
    check_schema(skill, schema, report)
    if not report.errors:  # invariants assume a schema-valid document
        check_invariants(skill, report)

    for warning in report.warnings:
        print(f"warning  {warning}")
    for error in report.errors:
        print(f"error    {error}")

    if report.errors:
        print(f"\n{args.skill}: INVALID ({len(report.errors)} error(s), {len(report.warnings)} warning(s))")
        return 1
    steps = len(skill["steps"])
    print(f"{args.skill}: valid ({steps} steps, {len(skill['assets'])} assets, {len(report.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
