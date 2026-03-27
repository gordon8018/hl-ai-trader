from pathlib import Path

from research.candidate_pack import write_candidate_pack


def test_write_candidate_pack_creates_required_files(tmp_path: Path):
    out = write_candidate_pack(
        output_dir=tmp_path,
        profile_name="V9_ar_20260327_run1",
        candidate_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.03},
        metrics={"score_total": 1.23},
        baseline_params={"AI_SIGNAL_DELTA_THRESHOLD": 0.05},
        risk_notes="drawdown stable",
    )

    assert (out / "candidate_profile.json").exists()
    assert (out / "report.md").exists()
    assert (out / "diff.md").exists()
    assert (out / "risk_notes.md").exists()
