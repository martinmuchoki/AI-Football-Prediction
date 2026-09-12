from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_THRESHOLD = 0.65
POLICY_VERSION = "phase5-epl-v1"


@dataclass(frozen=True)
class SelectionDecision:
    label: str
    prediction: str
    confidence: float
    threshold: float
    publish: bool
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_prediction(p_home: float, p_draw: float, p_away: float, *, threshold: float = DEFAULT_THRESHOLD) -> SelectionDecision:
    probs = {"H": float(p_home), "D": float(p_draw), "A": float(p_away)}
    if not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1")
    if any(v < 0 for v in probs.values()):
        raise ValueError("probabilities cannot be negative")
    total = sum(probs.values())
    if total <= 0:
        raise ValueError("probabilities must sum to a positive value")
    normalized = {k: v / total for k, v in probs.items()}
    prediction = max(normalized, key=normalized.get)
    confidence = normalized[prediction]
    publish = confidence >= threshold
    return SelectionDecision(
        label="HIGH_CONFIDENCE" if publish else "PASS",
        prediction=prediction,
        confidence=round(confidence, 8),
        threshold=float(threshold),
        publish=publish,
    )


def apply_selector_to_prediction_rows(rows: Iterable[dict[str, str]], *, model_prefix: str = "bookmaker_open", threshold: float = DEFAULT_THRESHOLD) -> list[dict[str, Any]]:
    output = []
    hk, dk, ak = (
        f"{model_prefix}_p_home",
        f"{model_prefix}_p_draw",
        f"{model_prefix}_p_away",
    )
    for source in rows:
        decision = select_prediction(
            float(source[hk]), float(source[dk]), float(source[ak]), threshold=threshold
        )
        row = dict(source)
        row.update({
            "selector_label": decision.label,
            "selector_prediction": decision.prediction,
            "selector_confidence": decision.confidence,
            "selector_threshold": decision.threshold,
            "selector_publish": int(decision.publish),
            "selector_policy_version": decision.policy_version,
        })
        output.append(row)
    return output


def run_selector_csv(*, input_path: str, output_path: str, model_prefix: str = "bookmaker_open", threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    destination = Path(output_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Prediction CSV not found: {source}")
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Prediction CSV is empty")
    selected = apply_selector_to_prediction_rows(rows, model_prefix=model_prefix, threshold=threshold)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0].keys()))
        writer.writeheader()
        writer.writerows(selected)
    high = sum(int(row["selector_publish"]) for row in selected)
    manifest = {
        "status": "success",
        "policy_version": POLICY_VERSION,
        "model_prefix": model_prefix,
        "threshold": threshold,
        "input_rows": len(selected),
        "high_confidence": high,
        "pass": len(selected) - high,
        "coverage": round(high / len(selected), 6),
        "output": str(destination),
        "final_holdout_touched": False,
    }
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest
