"""Offline rescoring, aggregate reports, and paired protocol sensitivity."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from .data import DataError, read_json, write_json
from .evaluation import extract_raw, score_primary, score_secondary
from .run_store import StoredRun, load_run, write_text


BOOTSTRAP_SEED = 4242
BOOTSTRAP_RESAMPLES = 2000


def rescore(run_id: str) -> Path:
    run = load_run(run_id)
    records = []
    for row in sorted(run.items, key=lambda item: (item["item_id"], item["sample_index"])):
        _, extraction = extract_raw(row["raw_output"], row["truncated"])
        answer = extraction["answer"]
        recomputed = {
            "primary": score_primary(answer, row["reference_answer"])["correct"],
            "secondary": score_secondary(answer, row["reference_answer"])["correct"],
        }
        stored = {role: row["verdicts"][role] for role in ("primary", "secondary")}
        records.append({"item_id": row["item_id"], "sample_index": row["sample_index"],
                        "stored": stored, "recomputed": recomputed, "match": stored == recomputed})
    output = run.directory / "rescore.json"
    write_json(output, {"rescore_version": "1", "run_id": run_id,
                        "all_match": all(row["match"] for row in records), "records": records})
    return output


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise DataError("cannot summarize an empty run", 3)
    position = probability * (len(ordered) - 1)
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def _bootstrap_means(values: list[float], seed: int = BOOTSTRAP_SEED) -> list[float]:
    generator = random.Random(seed)
    size = len(values)
    return [sum(values[generator.randrange(size)] for _ in range(size)) / size
            for _ in range(BOOTSTRAP_RESAMPLES)]


def _wilson(successes: int, count: int) -> list[float]:
    z = 1.959963984540054
    proportion = successes / count
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count)) / denominator
    return [center - half, center + half]


def _scorer_summary(run: StoredRun, role: str) -> dict[str, Any]:
    items = run.items
    if not items:
        raise DataError("cannot report an empty run", 3)
    per_seed: dict[int, list[bool]] = defaultdict(list)
    per_item: dict[str, list[bool]] = defaultdict(list)
    for row in items:
        verdict = row["verdicts"][role]
        per_seed[row["sample_index"]].append(verdict)
        per_item[row["item_id"]].append(verdict)
    accuracy = sum(row["verdicts"][role] for row in items) / len(items)
    seed_accuracy = {str(index): sum(verdicts) / len(verdicts)
                     for index, verdicts in sorted(per_seed.items())}
    if run.metadata["k"] == 1:
        interval = _wilson(sum(row["verdicts"][role] for row in items), len(items))
        return {"accuracy": accuracy, "interval_95": interval, "interval_method": "Wilson 95%",
                "per_seed": seed_accuracy}
    item_means = [sum(verdicts) / len(verdicts) for _, verdicts in sorted(per_item.items())]
    boot = _bootstrap_means(item_means)
    interval = [_percentile(boot, .025), _percentile(boot, .975)]
    mean = sum(item_means) / len(item_means)
    interval = [min(interval[0], mean), max(interval[1], mean)]
    spread = max(seed_accuracy.values()) - min(seed_accuracy.values())
    return {"accuracy": accuracy, "per_item_seed_mean": mean,
            "item_bootstrap_interval_95": interval, "interval_95": interval,
            "bootstrap_seed": BOOTSTRAP_SEED, "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "spread_across_seeds": spread, "per_seed": seed_accuracy}


def _provenance(protocol: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    def visit(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            if "provenance" in value:
                entries.append({"field": prefix, "provenance": value["provenance"]})
            for key, nested in value.items():
                if key != "provenance":
                    visit(nested, f"{prefix}.{key}" if prefix else key)
    visit(protocol, "")
    return entries


def _comparison_reading(value: float, primary: dict[str, Any], secondary: dict[str, Any],
                        sensitivity: dict[str, Any] | None) -> dict[str, Any]:
    low, high = primary["interval_95"]
    if low <= value <= high:
        return {"reading": "CONSISTENT", "published": value, "factors": []}
    primary_accuracy = primary["accuracy"]
    secondary_accuracy = secondary["accuracy"]
    factors = []
    if min(primary_accuracy, secondary_accuracy) <= value <= max(primary_accuracy, secondary_accuracy):
        factors.append("grader")
    if sensitivity:
        difference = value - primary_accuracy
        for comparison in sensitivity["comparisons"]:
            lo, hi = comparison["effect_interval_95"]
            if min(0, lo) <= difference <= max(0, hi):
                factors.append(comparison["factor"])
    return {"reading": "PROTOCOL-SENSITIVE" if factors else "UNEXPLAINED",
            "published": value, "factors": sorted(set(factors))}


def report(run_id: str, published: float | None = None, sensitivity_path: Path | None = None,
           out_dir: Path | None = None) -> list[Path]:
    if published is not None and (not math.isfinite(published) or not 0 <= published <= 1):
        raise DataError("published value must be a fraction in [0, 1]", 3)
    run = load_run(run_id)
    sensitivity = read_json(sensitivity_path) if sensitivity_path else None
    if sensitivity is not None and (sensitivity.get("sensitivity_version") != "1"
                                    or sensitivity.get("base_run") != run_id
                                    or sensitivity.get("base_protocol_sha256") != run.metadata["protocol_sha256"]):
        raise DataError("sensitivity file does not match this run", 2)
    primary = _scorer_summary(run, "primary")
    secondary = _scorer_summary(run, "secondary")
    rows = run.items
    count = len(rows)
    counts = {
        "format_failures": sum(row["extraction"]["status"] == "no_answer" and not row["truncated"] for row in rows),
        "truncations": sum(bool(row["truncated"]) for row in rows),
        "fallback_extractions": sum(row["extraction"]["status"] == "fallback" for row in rows),
        "thinking_rate": sum(bool(row["thinking_present"]) for row in rows) / count,
        "grader_disagreements": sum(row["verdicts"]["primary"] != row["verdicts"]["secondary"] for row in rows),
    }
    document: dict[str, Any] = {
        "report_version": "1", "run_id": run_id, "tier": run.metadata["tier"],
        "k": run.metadata["k"], "protocol_sha256": run.metadata["protocol_sha256"],
        "manifest_sha256": run.metadata["manifest_sha256"],
        "scorers": {"primary": primary, "secondary": secondary}, "counts": counts,
        "protocol_provenance": _provenance(run.protocol),
        "deviations": run.protocol.get("deviations", []),
        "interpretation": "Best-effort reproduction under a reconstructed public protocol; original protocol unknown.",
    }
    if published is not None:
        document["comparison"] = _comparison_reading(published, primary, secondary, sensitivity)
        low, high = primary["interval_95"]
        document["distance_from_published"] = primary["accuracy"] - published
        document["distance_interval_95"] = [low - published, high - published]
    output_dir = out_dir or run.directory
    json_path, md_path = output_dir / "report.json", output_dir / "report.md"
    lines = [f"# Evaluation report: {run_id}", "",
             f"Tier: {run.metadata['tier']}; samples per item: {run.metadata['k']}", "",
             "| Scorer | Accuracy | 95% interval |", "| --- | ---: | --- |",
             f"| Primary | {primary['accuracy']:.6f} | {primary['interval_95']} |",
             f"| Secondary | {secondary['accuracy']:.6f} | {secondary['interval_95']} |", "",
             f"Format failures: {counts['format_failures']}; truncations: {counts['truncations']}; "
             f"fallback extractions: {counts['fallback_extractions']}; thinking rate: {counts['thinking_rate']:.6f}; "
             f"grader disagreements: {counts['grader_disagreements']}"]
    if "comparison" in document:
        comparison = document["comparison"]
        lines.extend(("", f"Comparison reading: {comparison['reading']}",
                      f"Factors: {', '.join(comparison['factors']) or 'none'}",
                      f"Distance from published fraction: {document['distance_from_published']:.6f}",
                      f"Distance interval (95%): {document['distance_interval_95']}"))
    lines.extend(("", document["interpretation"]))
    lines.extend(("", "## Protocol provenance", "",
                  "| Field | Provenance |", "| --- | --- |"))
    lines.extend(f"| {entry['field']} | {entry['provenance']} |"
                 for entry in document["protocol_provenance"])
    lines.extend(("", "## Deviations", ""))
    lines.extend(str(deviation) for deviation in document["deviations"])
    if not document["deviations"]:
        lines.append("None recorded.")
    write_json(json_path, document)
    write_text(md_path, "\n".join(lines) + "\n")
    return [json_path, md_path]


def _without_provenance(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_provenance(nested) for key, nested in value.items()
                if key not in ("provenance", "protocol_version")}
    if isinstance(value, list):
        return [_without_provenance(nested) for nested in value]
    return value


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        if prefix == "model.artifact":
            return {prefix: value}
        flattened: dict[str, Any] = {}
        for key, nested in value.items():
            flattened.update(_flatten(nested, f"{prefix}.{key}" if prefix else key))
        return flattened
    if isinstance(value, list):
        return {prefix: value}
    return {prefix: value}


def _factor(base: dict[str, Any], variant: dict[str, Any]) -> tuple[str, Any, Any]:
    old = _flatten(_without_provenance(base))
    new = _flatten(_without_provenance(variant))
    changed = [key for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)]
    if len(changed) != 1:
        raise DataError(f"single-factor comparison requires exactly one difference; found {len(changed)}", 3)
    factor = changed[0]
    return factor, old.get(factor), new.get(factor)


def _item_scores(run: StoredRun) -> dict[str, float]:
    values: dict[str, list[bool]] = defaultdict(list)
    for row in run.items:
        values[row["item_id"]].append(row["verdicts"]["primary"])
    return {item_id: sum(verdicts) / len(verdicts) for item_id, verdicts in values.items()}


def sensitivity(base_id: str, variant_ids: list[str], out: Path) -> Path:
    base = load_run(base_id)
    base_scores = _item_scores(base)
    comparisons = []
    for variant_id in variant_ids:
        variant = load_run(variant_id)
        if (base.metadata["manifest_sha256"] != variant.metadata["manifest_sha256"]
                or base.metadata["tier"] != variant.metadata["tier"]):
            raise DataError("sensitivity runs have different manifests or tiers", 3)
        variant_scores = _item_scores(variant)
        if base_scores.keys() != variant_scores.keys():
            raise DataError("sensitivity runs have different item sets", 3)
        factor, old, new = _factor(base.protocol, variant.protocol)
        differences = [variant_scores[item_id] - base_scores[item_id] for item_id in sorted(base_scores)]
        effect = sum(differences) / len(differences)
        boot = _bootstrap_means(differences)
        low, high = _percentile(boot, .025), _percentile(boot, .975)
        comparisons.append({
            "variant_run": variant_id, "factor": factor,
            "base_value": old, "variant_value": new,
            "paired_items": len(differences),
            "base_accuracy": sum(base_scores.values()) / len(base_scores),
            "variant_accuracy": sum(variant_scores.values()) / len(variant_scores),
            "effect": effect, "effect_interval_95": [min(low, effect), max(high, effect)],
            "method": "paired bootstrap over items, primary scorer",
            "bootstrap_seed": BOOTSTRAP_SEED, "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        })
    write_json(out, {"sensitivity_version": "1", "base_run": base_id,
                     "base_protocol_sha256": base.metadata["protocol_sha256"],
                     "comparisons": comparisons})
    return out
