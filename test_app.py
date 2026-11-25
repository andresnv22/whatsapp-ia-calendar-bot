from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_webhook_verification_success(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret123")
    res = client.get("/webhook?hub.mode=subscribe&hub.verify_token=secret123&hub.challenge=999")
    assert res.status_code == 200
    assert res.json() == 999


def test_webhook_verification_forbidden(monkeypatch):
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "secret123")
    res = client.get("/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=999")
    assert res.status_code == 403


def test_receive_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="Hola, ¿qué día prefieres?"))]
    with patch("app.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_resp
        res = client.post(
            "/webhook/message",
            json={"sender": "+573001112233", "message": "Hola", "channel": "whatsapp"},
        )
    assert res.status_code == 200
    assert res.json()["reply"] == "Hola, ¿qué día prefieres?"
    assert res.json()["sent"] is False


def test_book_appointment_invalid_date():
    res = client.post(
        "/appointments",
        json={
            "client_name": "Laura",
            "client_phone": "+573001112233",
            "service": "Valoración",
            "date_iso": "fecha-mala",
        },
    )
    assert res.status_code == 400
