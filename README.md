# WhatsApp AI + Google Calendar Bot

Agente conversacional de **WhatsApp Cloud API (Meta)** en Python (FastAPI) que responde consultas 24/7 con OpenAI (tool-calling) y agenda citas directamente en **Google Calendar**, verificando disponibilidad real y bloqueando dobles reservas.

## Qué incluye
- **Verificación oficial de webhook de Meta** (`GET /webhook` y firma `X-Hub-Signature-256` con HMAC-SHA256 en `POST /webhook`).
- **Deduplicación e historial en SQLite**: evita responder dos veces ante reintentos de Meta y recuerda el contexto reciente por usuario.
- **Google Calendar real**: consulta bloques ocupados (`check_calendar_availability`), valida horarios de atención, fechas futuras y previene doble reserva (`BEGIN IMMEDIATE` + verificación en Calendar).
- **Envío real por WhatsApp Graph API v21.0**.

## Requisitos e instalación
```bash
uv sync
cp .env.example .env
# Configura OPENAI_API_KEY, WHATSAPP_*, y coloca credentials.json de Google Service Account
uv run uvicorn app:app --reload
```

## Pruebas automatizadas
```bash
uv run pytest
```
