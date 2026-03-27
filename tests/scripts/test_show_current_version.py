from io import StringIO
from contextlib import redirect_stdout, redirect_stderr

from scripts.show_current_version import format_version_payload
from scripts import show_current_version


def test_format_version_payload():
    text = format_version_payload({"version": "V9_ar_1", "stage": "canary_live"})
    assert "V9_ar_1" in text
    assert "canary_live" in text


def test_main_rejects_invalid_json():
    out = StringIO()
    err = StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = show_current_version.main(["--payload", "{not-json"])
    assert code == 2
    assert out.getvalue() == ""
    assert "invalid JSON payload" in err.getvalue()


def test_main_rejects_non_dict_json():
    out = StringIO()
    err = StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = show_current_version.main(["--payload", "[1, 2, 3]"])
    assert code == 2
    assert out.getvalue() == ""
    assert "payload must be a JSON object" in err.getvalue()
