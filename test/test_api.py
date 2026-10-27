from datetime import datetime
from types import SimpleNamespace

from app.api import resources


def test_health_responde_ok(client):
    respuesta = client.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.get_json()["status"] == "ok"
    assert respuesta.get_json()["database"] == "up"


def test_post_blacklists_agrega_email(client, auth_headers, monkeypatch):
    entrada = SimpleNamespace(
        id=1,
        email="ana@example.com",
        app_uuid="123e4567-e89b-12d3-a456-426614174000",
        blocked_reason="fraude",
        ip_address="127.0.0.1",
        created_at=datetime(2026, 1, 1),
    )

    monkeypatch.setattr(
        resources.service,
        "add_email",
        lambda **kwargs: entrada,
    )

    respuesta = client.post(
        "/blacklists",
        json={
            "email": "ana@example.com",
            "app_uuid": "123e4567-e89b-12d3-a456-426614174000",
            "blocked_reason": "fraude",
        },
        headers=auth_headers,
    )

    assert respuesta.status_code == 201
    assert respuesta.get_json()["blacklist"]["email"] == "ana@example.com"


def test_get_blacklists_encuentra_email(client, auth_headers, monkeypatch):
    entrada = SimpleNamespace(
        email="ana@example.com",
        blocked_reason="fraude",
    )

    monkeypatch.setattr(
        resources.service,
        "get_entry",
        lambda email: entrada,
    )

    respuesta = client.get(
        "/blacklists/ana@example.com",
        headers=auth_headers,
    )

    assert respuesta.status_code == 200
    assert respuesta.get_json()["is_blacklisted"] is True
    assert respuesta.get_json()["blocked_reason"] == "fraude"