import pytest


def test_application_state_detects_stale_writer_and_keeps_previous_data(tmp_path):
    from qx_stc_isp.vofa.app_state import AppStateStore
    path = tmp_path / "application.json"
    one, two = AppStateStore(path), AppStateStore(path)
    a, b = one.load(), two.load()
    assert a.revision == b.revision == 0
    saved = one.save({"history": [], "recent_workspace": "first.json"}, expected_revision=0)
    assert saved.revision == 1
    with pytest.raises(ValueError, match="修改"):
        two.save({"recent_workspace": "wrong.json"}, expected_revision=b.revision)
    assert one.load().data["recent_workspace"] == "first.json"


def test_history_validation_preserves_order_and_rejects_invalid_hex(tmp_path):
    from qx_stc_isp.vofa.app_state import validate_history
    history = [{"text": "AA 01", "mode": "hex", "ending": "none", "encoding": "utf-8"}]
    assert validate_history(history) == history
    with pytest.raises(ValueError):
        validate_history([{**history[0], "text": "GG"}])
