import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

load_dotenv()

app = FastAPI(
    title="WhatsApp / IG DM AI + Google Calendar Bot",
    description="Bot conversacional con IA que responde dudas 24/7 y agenda citas en Google Calendar.",
    version="1.0.0",
)

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "mi_token_secreto")

# Memoria simple en RAM para demo (en producción usar Redis o PostgreSQL)
DEMO_CALENDAR: list[dict] = []


class IncomingMessage(BaseModel):
    sender: str
    message: str
    channel: str = "whatsapp"


class AppointmentRequest(BaseModel):
    client_name: str
    client_phone: str
    service: str
    date_iso: str  # Ej: "2026-11-26T15:00:00"


def generate_ai_reply(user_message: str) -> str:
    """Genera respuesta con IA o en modo demo."""
    if DEMO_MODE or not os.getenv("OPENAI_API_KEY"):
        text = user_message.lower()
        if any(k in text for k in ["precio", "costo", "cuanto", "valor"]):
            return (
                "¡Hola! Nuestras consultas comienzan desde $80.000 COP. "
                "¿Te gustaría agendar una cita? Dime qué día y hora prefieres."
            )
        if any(k in text for k in ["cita", "agendar", "reservar", "hora", "mañana", "hoy"]):
            return (
                "¡Claro! Tengo disponibilidad mañana a las 10:00 AM y a las 3:00 PM. "
                "¿Cuál horario te queda mejor y cuál es tu nombre?"
            )
        return (
            "¡Hola! Soy el asistente virtual 24/7. Puedo darte información de servicios, "
            "precios y agendar tu cita al instante. ¿En qué te puedo ayudar?"
        )

    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {
                "role": "system",
                "content": (
                    "Eres un asistente comercial amable y conciso para un negocio local. "
                    "Responde dudas brevemente y guía al cliente a agendar una cita."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        temperature=0.4,
    )
    return resp.choices[0].message.content or "Gracias por escribirnos."


def create_calendar_event(req: AppointmentRequest) -> dict:
    """Crea un evento en Google Calendar (o lo simula en DEMO_MODE)."""
    start_dt = datetime.fromisoformat(req.date_iso)
    end_dt = start_dt + timedelta(minutes=45)

    if DEMO_MODE or not os.path.exists(os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")):
        event = {
            "id": f"demo-{len(DEMO_CALENDAR) + 1}",
            "summary": f"Cita: {req.service} - {req.client_name}",
            "phone": req.client_phone,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "mode": "demo",
        }
        DEMO_CALENDAR.append(event)
        return event

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=creds)
    body = {
        "summary": f"Cita: {req.service} - {req.client_name}",
        "description": f"Teléfono: {req.client_phone}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Bogota"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Bogota"},
    }
    created = (
        service.events()
        .insert(calendarId=os.getenv("GOOGLE_CALENDAR_ID", "primary"), body=body)
        .execute()
    )
    return {"id": created.get("id"), "htmlLink": created.get("htmlLink"), "mode": "google_calendar"}


@app.get("/health")
def health():
    return {"status": "ok", "demo_mode": DEMO_MODE}


@app.get("/webhook")
def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Verificación estándar de Meta (WhatsApp Cloud API / Instagram)."""
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return int(hub_challenge) if hub_challenge and hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@app.post("/webhook/message")
def receive_message(payload: IncomingMessage):
    """Recibe un mensaje de WhatsApp/IG y devuelve la respuesta generada por IA."""
    reply = generate_ai_reply(payload.message)
    return {
        "channel": payload.channel,
        "to": payload.sender,
        "reply": reply,
        "demo_mode": DEMO_MODE,
    }


@app.post("/appointments")
def book_appointment(req: AppointmentRequest):
    """Agenda una cita en Google Calendar."""
    try:
        event = create_calendar_event(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Formato de fecha inválido: {exc}") from exc
    return {"status": "scheduled", "event": event}


@app.get("/appointments")
def list_demo_appointments():
    """Lista las citas agendadas en modo demo."""
    return {"count": len(DEMO_CALENDAR), "appointments": DEMO_CALENDAR}
