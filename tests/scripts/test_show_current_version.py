from scripts.show_current_version import format_version_payload


def test_format_version_payload():
    text = format_version_payload({"version": "V9_ar_1", "stage": "canary_live"})
    assert "V9_ar_1" in text
    assert "canary_live" in text
