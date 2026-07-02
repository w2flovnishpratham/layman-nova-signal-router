from types import SimpleNamespace
import app.services.execution_router as er

def _res(success, error=None):
    return SimpleNamespace(success=success, order_id="OID", status="TRANSIT", error=error, interpreted_error=None, raw_response=None)

class FakeClient:
    def __init__(self, ok=True): self.calls=[]; self._ok=ok
    def modify_super_order(self, *, client_id, access_token, order_id, payload):
        self.calls.append(payload); return _res(self._ok, None if self._ok else "rejected")

def test_non_super_no_op():
    r = er.apply_manual_super_order_exit_levels({"exit_management": "SERVER"}, stop_loss_price=90, target_price=110)
    assert r["ok"] and r["reason"] == "not_super_order"

def test_paper_super_no_broker_call(monkeypatch):
    monkeypatch.setattr(er, "get_engine_mode", lambda *a, **k: "paper")
    monkeypatch.setattr(er, "_live_broker_client", lambda: (_ for _ in ()).throw(AssertionError("no broker in paper")))
    r = er.apply_manual_super_order_exit_levels({"exit_management": "DHAN_SUPER"}, stop_loss_price=90, target_price=110)
    assert r["ok"] and r["reason"] == "paper_local"

def test_live_super_modifies_both_legs(monkeypatch):
    fake = FakeClient(ok=True)
    monkeypatch.setattr(er, "get_engine_mode", lambda *a, **k: "live")
    monkeypatch.setattr(er, "get_dhan_credentials", lambda: SimpleNamespace(client_id="CID", access_token="TOK"))
    monkeypatch.setattr(er, "require_verified_live_egress", lambda: None)
    monkeypatch.setattr(er, "_live_broker_client", lambda: fake)
    monkeypatch.setattr(er, "log_order_event", lambda *a, **k: None)
    pos = {"exit_management": "DHAN_SUPER", "entry_order_id": "SUPER1"}
    r = er.apply_manual_super_order_exit_levels(pos, stop_loss_price=90.0, target_price=110.0)
    assert r["ok"] is True and len(fake.calls) == 2
    tcall = next(c for c in fake.calls if c["legName"] == "TARGET_LEG")
    scall = next(c for c in fake.calls if c["legName"] == "STOP_LOSS_LEG")
    assert tcall["targetPrice"] == 110.0 and scall["stopLossPrice"] == 90.0

def test_live_super_broker_failure_is_fail_closed(monkeypatch):
    fake = FakeClient(ok=False)
    monkeypatch.setattr(er, "get_engine_mode", lambda *a, **k: "live")
    monkeypatch.setattr(er, "get_dhan_credentials", lambda: SimpleNamespace(client_id="CID", access_token="TOK"))
    monkeypatch.setattr(er, "require_verified_live_egress", lambda: None)
    monkeypatch.setattr(er, "_live_broker_client", lambda: fake)
    monkeypatch.setattr(er, "log_order_event", lambda *a, **k: None)
    r = er.apply_manual_super_order_exit_levels({"exit_management": "DHAN_SUPER", "entry_order_id": "S1"}, stop_loss_price=90.0, target_price=110.0)
    assert r["ok"] is False and r.get("message")

def test_live_super_missing_order_id(monkeypatch):
    monkeypatch.setattr(er, "get_engine_mode", lambda *a, **k: "live")
    r = er.apply_manual_super_order_exit_levels({"exit_management": "DHAN_SUPER", "entry_order_id": ""}, stop_loss_price=90.0, target_price=110.0)
    assert r["ok"] is False and r["reason"] == "missing_super_order_id"
