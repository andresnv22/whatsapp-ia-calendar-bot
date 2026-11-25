# WhatsApp / Instagram DM AI + Google Calendar Bot

Demo en Python (FastAPI) para negocios locales (clínicas, odontólogos, barberías, inmobiliarias, restaurantes) que genera respuestas con IA y puede crear citas en Google Calendar.

**Estado:** el endpoint `/webhook/message` acepta un JSON simplificado y genera una respuesta, pero **no envía mensajes a WhatsApp o Instagram** ni procesa el formato real de sus webhooks. El evento de Calendar se crea solo con credenciales configuradas; de lo contrario se simula en memoria. Úsalo como demo hasta completar y verificar esas integraciones.

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
