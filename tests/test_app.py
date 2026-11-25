import hashlib
import hmac
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import app as bot_app

client = TestClient(bot_app.app)


def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_webhook_verification_success(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    res = client.get("/webhook?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=12345")
    assert res.status_code == 200
    assert res.json() == 12345


def test_webhook_rejects_invalid_hmac(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "app-secret")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    res = client.post(
        "/webhook",
        content=b"{}",
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert res.status_code == 403


def test_webhook_deduplicates_meta_retries(tmp_path, monkeypatch):
    secret = "app-secret"
    monkeypatch.setenv("WHATSAPP_APP_SECRET", secret)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("WHATSAPP_TOKEN", "wa-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "999")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "wamid.123",
                                    "from": "573001112233",
                                    "type": "text",
                                    "text": {"body": "Hola"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = sign_payload(secret, raw)

    mock_msg = MagicMock(tool_calls=None, content="¡Hola! ¿En qué te ayudo?")
    mock_resp = MagicMock(choices=[MagicMock(message=mock_msg)])

    with patch("app.OpenAI") as mock_openai, patch("app.httpx.post") as mock_post:
        mock_openai.return_value.chat.completions.create.return_value = mock_resp
        mock_post.return_value.json.return_value = {"messages": [{"id": "out.1"}]}
        mock_post.return_value.raise_for_status.return_value = None

        r1 = client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig})
        r2 = client.post("/webhook", content=raw, headers={"X-Hub-Signature-256": sig})

    assert r1.status_code == 200
    assert r1.json()["processed"] == 1
    assert r2.status_code == 200
    assert r2.json()["processed"] == 0
    mock_post.assert_called_once()


def test_validate_slot_rejects_past_and_out_of_hours(tmp_path):
    settings = {
        "timezone": "America/Bogota",
        "open_hour": 8,
        "close_hour": 18,
        "slot_minutes": 45,
        "database_path": str(tmp_path / "test.db"),
    }
    with pytest.raises(ValueError, match="ya pasó"):
        bot_app.validate_slot_datetime("2020-01-01T10:00:00", settings)

    tz = ZoneInfo("America/Bogota")
    future_day = datetime.now(tz) + timedelta(days=2)
    while future_day.weekday() == 6:
        future_day += timedelta(days=1)
    late_iso = future_day.replace(hour=22, minute=0, second=0, microsecond=0).isoformat()
    with pytest.raises(ValueError, match="fuera de atención"):
        bot_app.validate_slot_datetime(late_iso, settings)


def test_book_calendar_prevents_double_booking(tmp_path):
    tz = ZoneInfo("America/Bogota")
    future_day = datetime.now(tz) + timedelta(days=3)
    while future_day.weekday() == 6:
        future_day += timedelta(days=1)
    slot_iso = future_day.replace(hour=10, minute=0, second=0, microsecond=0).isoformat()

    settings = {
        "timezone": "America/Bogota",
        "open_hour": 8,
        "close_hour": 18,
        "slot_minutes": 45,
        "google_calendar_id": "primary",
        "google_credentials_file": "unused.json",
        "database_path": str(tmp_path / "bookings.db"),
    }

    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    fake_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "evt_1",
        "htmlLink": "https://calendar.google.com/event?eid=1",
    }

    with patch("app.get_calendar_service", return_value=fake_service):
        created = bot_app.book_calendar_appointment("57300111", "Ana", "Valoración", slot_iso, settings)
        assert created["id"] == "evt_1"

        with pytest.raises(ValueError, match="ya fue reservado"):
            bot_app.book_calendar_appointment("57300999", "Luis", "Valoración", slot_iso, settings)
