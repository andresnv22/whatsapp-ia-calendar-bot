from fastapi.testclient import TestClient

import app as module


def test_generated_reply_does_not_claim_to_be_sent(monkeypatch):
    monkeypatch.setattr(module, "DEMO_MODE", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    response = TestClient(module.app).post(
        "/webhook/message", json={"sender": "+573001112233", "message": "Hola", "channel": "whatsapp"}
    )
    assert response.status_code == 200
    assert response.json()["sent"] is False


def test_production_rejects_unimplemented_meta_webhook(monkeypatch):
    monkeypatch.setattr(module, "DEMO_MODE", False)
    response = TestClient(module.app).post(
        "/webhook/message", json={"sender": "+573001112233", "message": "Hola", "channel": "whatsapp"}
    )
    assert response.status_code == 501


def test_verification_requires_configured_token(monkeypatch):
    monkeypatch.setattr(module, "VERIFY_TOKEN", None)
    response = TestClient(module.app).get(
        "/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "123"}
    )
    assert response.status_code == 403
