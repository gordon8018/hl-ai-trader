from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _resolve_pack_dir(output_dir: Path, profile_name: str) -> Path:
    if not profile_name or profile_name.strip() != profile_name:
        raise ValueError("invalid_profile_name")

    profile_path = Path(profile_name)
    if profile_path.is_absolute() or len(profile_path.parts) != 1 or profile_path.name != profile_name:
        raise ValueError("invalid_profile_name")
    if profile_name in {".", ".."}:
        raise ValueError("invalid_profile_name")

    output_root = output_dir.resolve()
    pack_dir = (output_root / profile_name).resolve()
    if output_root not in pack_dir.parents:
        raise ValueError("invalid_profile_name")
    return pack_dir


def _build_diff(candidate: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    keys = sorted(set(candidate) | set(baseline))
    lines = [
        "# Candidate Diff",
        "",
        "| key | baseline | candidate |",
        "| --- | --- | --- |",
    ]
    for key in keys:
        lines.append(f"| {key} | {baseline.get(key)} | {candidate.get(key)} |")
    return "\n".join(lines) + "\n"


def write_candidate_pack(
    output_dir: Path,
    profile_name: str,
    candidate_params: Dict[str, Any],
    metrics: Dict[str, Any],
    baseline_params: Dict[str, Any],
    risk_notes: str,
) -> Path:
    pack_dir = _resolve_pack_dir(output_dir, profile_name)
    pack_dir.mkdir(parents=True, exist_ok=True)

    candidate_profile = {
        "profile_name": profile_name,
        "params": candidate_params,
        "metrics": metrics,
    }
    (pack_dir / "candidate_profile.json").write_text(
        json.dumps(candidate_profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (pack_dir / "report.md").write_text(
        "\n".join(
            [
                "# Candidate Report",
                "",
                "## Metrics",
                "",
                "```json",
                json.dumps(metrics, ensure_ascii=False, indent=2),
                "```",
                "",
                f"- profile: `{profile_name}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (pack_dir / "diff.md").write_text(
        _build_diff(candidate_params, baseline_params),
        encoding="utf-8",
    )
    (pack_dir / "risk_notes.md").write_text(
        risk_notes.rstrip() + "\n",
        encoding="utf-8",
    )
    return pack_dir
