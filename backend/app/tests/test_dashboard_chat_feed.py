import json
from pathlib import Path

from app.routers import dashboard
from app.services import audit_logger


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")


def _install_log_files(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    log_files = {
        "webhook": tmp_path / "webhook_events.jsonl",
        "order": tmp_path / "order_events.jsonl",
        "audit": tmp_path / "audit_events.jsonl",
        "error": tmp_path / "errors.jsonl",
    }
    monkeypatch.setattr(audit_logger, "LOG_FILES", log_files)
    return log_files


def _normalized_event(signal_id: str) -> dict:
    return {
        "timestamp": "2026-06-03T07:05:04+00:00",
        "event_type": "WEBHOOK_NORMALIZED",
        "signal_id": signal_id,
        "normalized_action": "ENTRY",
        "normalized_side": "BUY",
        "normalized_qty": 65,
        "normalized_symbol": "NIFTY",
        "normalized_strike": 23300,
        "normalized_expiry": "2026-06-02",
        "normalized_option_side": "CE",
        "payload_format": "NOVA",
    }


def _ghost_sync_events(count: int) -> list[dict]:
    return [
        {
            "timestamp": f"2026-06-03T07:{6 + index // 60:02d}:{index % 60:02d}+00:00",
            "event": "GHOST_POSITION_SYNC",
            "status": "ok",
        }
        for index in range(count)
    ]


def test_chat_feed_uses_blocked_audit_event_when_order_event_is_not_recent(monkeypatch, tmp_path):
    signal_id = "TV-1780470000000-NIFTY-CE"
    log_files = _install_log_files(monkeypatch, tmp_path)

    _write_jsonl(log_files["webhook"], [_normalized_event(signal_id)])
    _write_jsonl(
        log_files["audit"],
        [
            {
                "timestamp": "2026-06-03T07:05:05+00:00",
                "event_type": "BLOCKED",
                "severity": "WARNING",
                "message": "Dhan Super Order blocked: could not fetch option LTP for broker-side SL/TP.",
                "metadata": {"signal_id": signal_id},
            }
        ],
    )
    _write_jsonl(log_files["order"], _ghost_sync_events(150))

    feed = dashboard.chat_feed(today_only=False)
    texts = [item.get("text", "") for item in feed]

    assert any("Trade blocked: Dhan Super Order blocked" in text for text in texts)
    assert not any("Routing did not write an order event yet" in text for text in texts)


def test_chat_feed_reads_enough_order_events_to_find_signal_block(monkeypatch, tmp_path):
    signal_id = "TV-1780470000000-NIFTY-CE"
    log_files = _install_log_files(monkeypatch, tmp_path)

    _write_jsonl(log_files["webhook"], [_normalized_event(signal_id)])
    _write_jsonl(
        log_files["order"],
        [
            {
                "timestamp": "2026-06-03T07:05:05+00:00",
                "phase": "blocked",
                "signal_id": signal_id,
                "reason": "Dhan Super Order blocked: could not fetch option LTP for broker-side SL/TP.",
            },
            *_ghost_sync_events(150),
        ],
    )

    feed = dashboard.chat_feed(today_only=False)
    texts = [item.get("text", "") for item in feed]

    assert any("Trade blocked: Dhan Super Order blocked" in text for text in texts)
    assert not any("Routing did not write an order event yet" in text for text in texts)
