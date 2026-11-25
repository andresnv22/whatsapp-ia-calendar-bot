import hashlib
import hmac
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from openai import OpenAI

load_dotenv()

logger = logging.getLogger("whatsapp_calendar_bot")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="WhatsApp AI + Google Calendar Bot",
    description="Agente de WhatsApp Cloud API con verificación HMAC, deduplicación en SQLite, tool-calling con OpenAI y agenda real en Google Calendar.",
    version="1.0.0",
)


def get_settings() -> dict:
    return {
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "whatsapp_token": os.getenv("WHATSAPP_TOKEN", ""),
        "whatsapp_phone_number_id": os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        "whatsapp_verify_token": os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
        "whatsapp_app_secret": os.getenv("WHATSAPP_APP_SECRET", ""),
        "google_credentials_file": os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        "google_calendar_id": os.getenv("GOOGLE_CALENDAR_ID", "primary"),
        "timezone": os.getenv("TIMEZONE", "America/Bogota"),
        "open_hour": int(os.getenv("OPEN_HOUR", "8")),
        "close_hour": int(os.getenv("CLOSE_HOUR", "18")),
        "slot_minutes": int(os.getenv("SLOT_MINUTES", "45")),
        "database_path": os.getenv("DATABASE_PATH", "whatsapp_calendar.db"),
    }


def init_db(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_messages (
                message_id TEXT PRIMARY KEY,
                sender TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookings_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                client_name TEXT NOT NULL,
                service TEXT NOT NULL,
                slot_start TEXT NOT NULL UNIQUE,
                google_event_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def verify_meta_signature(raw_body: bytes, signature_header: Optional[str], app_secret: str) -> None:
    if not app_secret:
        raise HTTPException(status_code=500, detail="WHATSAPP_APP_SECRET is not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=403, detail="Missing or malformed X-Hub-Signature-256")
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="Invalid X-Hub-Signature-256")


def mark_message_if_new(db_path: str, message_id: str, sender: str) -> bool:
    """Devuelve True si el mensaje es nuevo; False si Meta reenvió un duplicado."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO processed_messages (message_id, sender, created_at) VALUES (?, ?, ?)",
            (message_id, sender, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        return cur.rowcount == 1


def save_turn(db_path: str, sender: str, role: str, content: str) -> None:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO conversation_history (sender, role, content, created_at) VALUES (?, ?, ?, ?)",
            (sender, role, content, datetime.now(UTC).isoformat()),
        )
        conn.commit()


def load_recent_history(db_path: str, sender: str, limit: int = 8) -> list[dict]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT role, content FROM conversation_history WHERE sender = ? ORDER BY id DESC LIMIT ?",
            (sender, limit),
        ).fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


def get_calendar_service(settings: dict):
    creds_file = settings["google_credentials_file"]
    if not os.path.exists(creds_file):
        raise RuntimeError(f"Google credentials file not found: {creds_file}")
    creds = service_account.Credentials.from_service_account_file(
        creds_file,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    return build("calendar", "v3", credentials=creds)


def validate_slot_datetime(date_iso: str, settings: dict) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings["timezone"])
    clean_iso = date_iso.replace("Z", "+00:00")
    start_dt = datetime.fromisoformat(clean_iso)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=tz)
    else:
        start_dt = start_dt.astimezone(tz)

    now = datetime.now(tz)
    if start_dt <= now:
        raise ValueError("La fecha solicitada ya pasó; elige una fecha y hora futura.")
    if start_dt.weekday() == 6:
        raise ValueError("No atendemos domingos; elige entre lunes y sábado.")
    if not (settings["open_hour"] <= start_dt.hour < settings["close_hour"]):
        raise ValueError(
            f"Horario fuera de atención ({settings['open_hour']}:00 - {settings['close_hour']}:00)."
        )

    end_dt = start_dt + timedelta(minutes=settings["slot_minutes"])
    return start_dt, end_dt


def check_calendar_availability(date_str: str, settings: dict) -> dict:
    """Consulta horarios ocupados en Google Calendar para una fecha YYYY-MM-DD."""
    tz = ZoneInfo(settings["timezone"])
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=settings["open_hour"], minute=0, second=0, tzinfo=tz
    )
    day_end = day_start.replace(hour=settings["close_hour"], minute=0, second=0)

    service = get_calendar_service(settings)
    events_result = (
        service.events()
        .list(
            calendarId=settings["google_calendar_id"],
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    busy_slots = [
        {
            "start": e["start"].get("dateTime", e["start"].get("date")),
            "end": e["end"].get("dateTime", e["end"].get("date")),
        }
        for e in events_result.get("items", [])
    ]
    return {
        "date": date_str,
        "timezone": settings["timezone"],
        "open_hour": settings["open_hour"],
        "close_hour": settings["close_hour"],
        "busy_slots": busy_slots,
    }


def book_calendar_appointment(
    sender: str, client_name: str, service_name: str, date_iso: str, settings: dict
) -> dict:
    """Verifica disponibilidad real en Google Calendar, bloquea doble reserva en SQLite y crea el evento."""
    start_dt, end_dt = validate_slot_datetime(date_iso, settings)
    slot_key = start_dt.isoformat()
    db_path = settings["database_path"]
    init_db(db_path)

    service = get_calendar_service(settings)
    conflicts = (
        service.events()
        .list(
            calendarId=settings["google_calendar_id"],
            timeMin=start_dt.isoformat(),
            timeMax=end_dt.isoformat(),
            singleEvents=True,
        )
        .execute()
        .get("items", [])
    )
    if conflicts:
        raise ValueError("Ese horario ya está ocupado en Google Calendar. Elige otra hora.")

    with sqlite3.connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM bookings_audit WHERE slot_start = ?", (slot_key,)
        ).fetchone()
        if existing:
            raise ValueError("Ese horario ya fue reservado por otro cliente.")

        body = {
            "summary": f"Cita: {service_name} - {client_name}",
            "description": f"WhatsApp: {sender}",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": settings["timezone"]},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": settings["timezone"]},
        }
        created = (
            service.events()
            .insert(calendarId=settings["google_calendar_id"], body=body)
            .execute()
        )
        event_id = created.get("id", "")
        conn.execute(
            "INSERT INTO bookings_audit (sender, client_name, service, slot_start, google_event_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sender, client_name, service_name, slot_key, event_id, datetime.now(UTC).isoformat()),
        )
        conn.commit()

    return {"id": event_id, "htmlLink": created.get("htmlLink", ""), "start": slot_key}


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_calendar_availability",
            "description": "Checks busy slots in Google Calendar for a specific date (YYYY-MM-DD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["date_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_calendar_appointment",
            "description": "Books an appointment in Google Calendar after confirming availability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {"type": "string"},
                    "service_name": {"type": "string"},
                    "date_iso": {
                        "type": "string",
                        "description": "ISO 8601 datetime, e.g. 2026-11-26T15:00:00",
                    },
                },
                "required": ["client_name", "service_name", "date_iso"],
            },
        },
    },
]


def run_agent(sender: str, user_message: str, settings: dict) -> str:
    if not settings["openai_api_key"]:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings["openai_api_key"])
    tz = ZoneInfo(settings["timezone"])
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")

    history = load_recent_history(settings["database_path"], sender)
    messages = [
        {
            "role": "system",
            "content": (
                f"Eres un asistente comercial de WhatsApp. Fecha y hora actual: {now_str}. "
                "Siempre usa check_calendar_availability antes de confirmar un horario y "
                "book_calendar_appointment para agendar cuando el cliente dé nombre, servicio, fecha y hora. "
                "Responde en el mismo idioma del cliente, breve y claro."
            ),
        },
        *history,
        {"role": "user", "content": user_message},
    ]

    for _ in range(4):
        resp = client.chat.completions.create(
            model=settings["openai_model"],
            messages=messages,
            tools=TOOLS,
            temperature=0.3,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            reply_text = msg.content or "Gracias por escribirnos."
            save_turn(settings["database_path"], sender, "user", user_message)
            save_turn(settings["database_path"], sender, "assistant", reply_text)
            return reply_text

        messages.append(msg)
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            try:
                if tc.function.name == "check_calendar_availability":
                    result = check_calendar_availability(args["date_str"], settings)
                elif tc.function.name == "book_calendar_appointment":
                    result = book_calendar_appointment(
                        sender=sender,
                        client_name=args["client_name"],
                        service_name=args["service_name"],
                        date_iso=args["date_iso"],
                        settings=settings,
                    )
                else:
                    result = {"error": f"Unknown tool: {tc.function.name}"}
            except Exception as exc:
                result = {"error": str(exc)}

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return "Por favor indícame nuevamente el día y hora que prefieres."


def send_whatsapp_text(to_phone: str, body: str, settings: dict) -> dict:
    if not settings["whatsapp_token"] or not settings["whatsapp_phone_number_id"]:
        raise RuntimeError("WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID must be configured")

    url = f"https://graph.facebook.com/v21.0/{settings['whatsapp_phone_number_id']}/messages"
    headers = {
        "Authorization": f"Bearer {settings['whatsapp_token']}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": body},
    }
    response = httpx.post(url, headers=headers, json=payload, timeout=15.0)
    response.raise_for_status()
    return response.json()


def extract_whatsapp_messages(payload: dict) -> list[dict]:
    extracted = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") == "text":
                    extracted.append(
                        {
                            "message_id": msg["id"],
                            "sender": msg["from"],
                            "text": msg["text"]["body"],
                        }
                    )
    return extracted


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/webhook")
def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    settings = get_settings()
    if not settings["whatsapp_verify_token"]:
        raise HTTPException(status_code=500, detail="WHATSAPP_VERIFY_TOKEN is not configured")
    if hub_mode == "subscribe" and hub_verify_token == settings["whatsapp_verify_token"]:
        return int(hub_challenge) if hub_challenge and hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=403, detail="Invalid verification token")


@app.post("/webhook")
async def receive_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
):
    settings = get_settings()
    raw_body = await request.body()
    verify_meta_signature(raw_body, x_hub_signature_256, settings["whatsapp_app_secret"])

    payload = json.loads(raw_body.decode("utf-8"))
    messages = extract_whatsapp_messages(payload)
    processed = 0

    for item in messages:
        if not mark_message_if_new(settings["database_path"], item["message_id"], item["sender"]):
            continue
        reply_text = run_agent(item["sender"], item["text"], settings)
        send_whatsapp_text(item["sender"], reply_text, settings)
        processed += 1

    return {"status": "ok", "processed": processed}
