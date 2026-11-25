import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

app = FastAPI(
    title="WhatsApp / IG DM AI + Google Calendar Bot",
    description="Genera borradores de respuesta y crea citas en Google Calendar; Meta messaging is not implemented.",
    version="1.0.0",
)


class IncomingMessage(BaseModel):
    sender: str
    message: str
    channel: str = "whatsapp"


class AppointmentRequest(BaseModel):
    client_name: str
    client_phone: str
    service: str
    date_iso: str  # Ej: "2026-11-26T15:00:00"


def get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY no está configurada")
    return OpenAI(api_key=api_key)


def get_calendar_service():
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    if not os.path.exists(creds_file):
        raise HTTPException(
            status_code=500,
            detail=f"Archivo de credenciales de Google Calendar no encontrado: {creds_file}",
        )
    creds = service_account.Credentials.from_service_account_file(
        creds_file,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds)


def generate_ai_reply(user_message: str) -> str:
    """Genera respuesta usando OpenAI."""
    client = get_openai_client()
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
    content = resp.choices[0].message.content
    if not content:
        raise HTTPException(status_code=502, detail="Respuesta vacía de OpenAI")
    return content


def create_calendar_event(req: AppointmentRequest) -> dict:
    """Crea un evento real en Google Calendar."""
    start_dt = datetime.fromisoformat(req.date_iso)
    end_dt = start_dt + timedelta(minutes=45)
    timezone = os.getenv("GOOGLE_TIMEZONE", "America/Bogota")

    service = get_calendar_service()
    body = {
        "summary": f"Cita: {req.service} - {req.client_name}",
        "description": f"Teléfono: {req.client_phone}",
        "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
    }
    created = (
        service.events()
        .insert(calendarId=os.getenv("GOOGLE_CALENDAR_ID", "primary"), body=body)
        .execute()
    )
    return {"id": created.get("id"), "htmlLink": created.get("htmlLink")}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/webhook")
def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    """Verificación estándar de Meta (WhatsApp Cloud API / Instagram)."""
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN")
    if not verify_token:
        raise HTTPException(status_code=500, detail="WHATSAPP_VERIFY_TOKEN no está configurado")
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        return int(hub_challenge) if hub_challenge and hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@app.post("/webhook/message")
def receive_message(payload: IncomingMessage):
    """Genera una respuesta, pero no la envía a WhatsApp o Instagram."""
    reply = generate_ai_reply(payload.message)
    return {"channel": payload.channel, "to": payload.sender, "reply": reply, "sent": False}


@app.post("/appointments")
def book_appointment(req: AppointmentRequest):
    """Agenda una cita real en Google Calendar."""
    try:
        datetime.fromisoformat(req.date_iso)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Formato de fecha inválido: {exc}") from exc
    event = create_calendar_event(req)
    return {"status": "scheduled", "event": event}
