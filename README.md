# WhatsApp / Instagram DM AI + Google Calendar Bot

Bot en Python (FastAPI) para negocios locales (clínicas, odontólogos, barberías, inmobiliarias, restaurantes) que responde preguntas 24/7 con IA y agenda citas automáticamente en Google Calendar.

## Características
- **Webhook compatible con Meta (WhatsApp Cloud API / Instagram DM)**.
- **Respuestas con IA** (OpenAI `gpt-4o-mini` o modo demo local sin API keys).
- **Agendamiento automático en Google Calendar** (Service Account o modo demo en memoria).

## Instalación rápida
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --reload
```

## Probar en modo demo (sin API keys)
1. Salud del servidor:
```bash
curl http://127.0.0.1:8000/health
```
2. Simular mensaje de WhatsApp:
```bash
curl -X POST http://127.0.0.1:8000/webhook/message \
  -H "Content-Type: application/json" \
  -d '{"sender": "+573001112233", "message": "Hola, quiero agendar una cita", "channel": "whatsapp"}'
```
3. Agendar cita:
```bash
curl -X POST http://127.0.0.1:8000/appointments \
  -H "Content-Type: application/json" \
  -d '{"client_name": "Laura Gómez", "client_phone": "+573001112233", "service": "Valoración", "date_iso": "2026-11-26T15:00:00"}'
```
