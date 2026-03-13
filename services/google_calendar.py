from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from utils.logger import get_logger

logger = get_logger("GoogleCalendarService")

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
except ImportError:
    Request = None
    Credentials = None
    build = None

SCOPES = ["https://www.googleapis.com/auth/calendar"]
WEEKDAY_MAP = {
    "segunda": 0,
    "terca": 1,
    "terça": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}
PERIOD_HOUR_MAP = {
    "de manha": 9,
    "da manha": 9,
    "pela manha": 9,
    "pela manhã": 9,
    "a tarde": 15,
    "à tarde": 15,
    "de tarde": 15,
    "a noite": 19,
    "à noite": 19,
    "de noite": 19,
}


class CalendarIntegrationError(RuntimeError):
    pass


@dataclass
class CalendarSlot:
    start: datetime
    end: datetime

    def label(self) -> str:
        return self.start.strftime("%d/%m %H:%M")


class GoogleCalendarService:
    def __init__(
        self,
        credentials_file: str | None = None,
        token_file: str | None = None,
        calendar_id: str | None = None,
        timezone_name: str | None = None,
    ) -> None:
        self.credentials_file = credentials_file or os.getenv("GOOGLE_CLIENT_SECRET_FILE", "credentials.json")
        self.token_file = token_file or os.getenv("GOOGLE_TOKEN_FILE", "token.json")
        self.calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self.timezone_name = timezone_name or os.getenv("GOOGLE_TIMEZONE", "America/Sao_Paulo")
        self.timezone = ZoneInfo(self.timezone_name)

    def is_configured(self) -> bool:
        return all(
            [
                build is not None,
                Credentials is not None,
                os.path.exists(self.credentials_file),
                os.path.exists(self.token_file),
            ]
        )

    def get_service(self):
        if build is None or Credentials is None or Request is None:
            raise CalendarIntegrationError("Dependencias do Google Calendar nao estao instaladas.")
        if not os.path.exists(self.token_file):
            raise CalendarIntegrationError(f"Arquivo de token nao encontrado: {self.token_file}")

        creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise CalendarIntegrationError("Token do Google Calendar invalido ou expirado sem refresh token.")

        return build("calendar", "v3", credentials=creds)

    def find_available_slots(
        self,
        preferred_text: str | None,
        duration_minutes: int = 30,
        limit: int = 3,
    ) -> tuple[datetime | None, list[CalendarSlot]]:
        preferred_start = parse_preferred_datetime(preferred_text, self.timezone)
        window_start = preferred_start or datetime.now(self.timezone)
        window_start = ceil_to_next_half_hour(window_start)
        window_end = window_start + timedelta(days=7)

        service = self.get_service()
        busy_response = (
            service.freebusy()
            .query(
                body={
                    "timeMin": window_start.isoformat(),
                    "timeMax": window_end.isoformat(),
                    "timeZone": self.timezone_name,
                    "items": [{"id": self.calendar_id}],
                }
            )
            .execute()
        )
        busy_ranges = busy_response["calendars"][self.calendar_id].get("busy", [])

        slots: list[CalendarSlot] = []
        cursor = window_start
        while cursor + timedelta(minutes=duration_minutes) <= window_end and len(slots) < limit:
            end = cursor + timedelta(minutes=duration_minutes)
            if is_business_slot(cursor, end) and is_slot_free(cursor, end, busy_ranges):
                slots.append(CalendarSlot(start=cursor, end=end))
            cursor += timedelta(minutes=30)

        return preferred_start, slots

    def create_event(
        self,
        summary: str,
        start: datetime,
        end: datetime,
        description: str | None = None,
    ) -> dict:
        service = self.get_service()
        event = {
            "summary": summary,
            "description": description or "Consulta criada automaticamente pelo clinic-agent.",
            "start": {"dateTime": start.isoformat(), "timeZone": self.timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": self.timezone_name},
        }
        return service.events().insert(calendarId=self.calendar_id, body=event).execute()


def normalize_preferred_datetime_text(preferred_text: str | None, timezone: ZoneInfo) -> str | None:
    parsed = parse_preferred_datetime(preferred_text, timezone)
    if not parsed:
        return None
    return parsed.strftime("%d/%m/%Y %H:%M")


def parse_preferred_datetime(preferred_text: str | None, timezone: ZoneInfo) -> datetime | None:
    if not preferred_text:
        return None

    lowered = preferred_text.strip().lower()
    now = datetime.now(timezone)
    date_candidate: datetime | None = None

    if "amanha" in lowered or "amanhã" in lowered:
        date_candidate = now + timedelta(days=1)
    else:
        weekday = next((value for key, value in WEEKDAY_MAP.items() if key in lowered), None)
        if weekday is not None:
            days_ahead = (weekday - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            date_candidate = now + timedelta(days=days_ahead)

    explicit_date = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", lowered)
    if explicit_date:
        day, month, year = explicit_date.groups()
        parsed_year = int(year) if year else now.year
        if parsed_year < 100:
            parsed_year += 2000
        date_candidate = datetime(parsed_year, int(month), int(day), tzinfo=timezone)

    if date_candidate is None:
        return None

    hour: int | None = None
    minute = 0
    explicit_hour_patterns = [
        r"(?:as|às)\s*(\d{1,2})(?:(?::(\d{2}))|h(?:(\d{2}))?)?(?:\s*horas?)?\b",
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+(\d{1,2})(?:(?::(\d{2}))|h(?:(\d{2}))?)?(?:\s*horas?)?\b",
        r"\bamanh[ãa]\s+(?:as|às)?\s*(\d{1,2})(?:(?::(\d{2}))|h(?:(\d{2}))?)?(?:\s*horas?)?\b",
    ]
    for pattern in explicit_hour_patterns:
        explicit_hour = re.search(pattern, lowered)
        if explicit_hour:
            hour = int(explicit_hour.group(1))
            minute = int(explicit_hour.group(2) or explicit_hour.group(3) or 0)
            break

    if hour is None:
        for period, mapped_hour in PERIOD_HOUR_MAP.items():
            if period in lowered:
                hour = mapped_hour
                break

    if hour is None:
        return None

    return datetime(
        date_candidate.year,
        date_candidate.month,
        date_candidate.day,
        hour,
        minute,
        tzinfo=timezone,
    )


def ceil_to_next_half_hour(value: datetime) -> datetime:
    rounded = value.replace(second=0, microsecond=0)
    if rounded.minute == 0 or rounded.minute == 30:
        return rounded
    if rounded.minute < 30:
        return rounded.replace(minute=30)
    return (rounded + timedelta(hours=1)).replace(minute=0)


def is_business_slot(start: datetime, end: datetime) -> bool:
    if start.weekday() >= 5:
        return False
    opening = start.replace(hour=8, minute=0, second=0, microsecond=0)
    lunch_start = start.replace(hour=12, minute=0, second=0, microsecond=0)
    lunch_end = start.replace(hour=13, minute=0, second=0, microsecond=0)
    closing = start.replace(hour=18, minute=0, second=0, microsecond=0)

    if start < opening or end > closing:
        return False
    if start < lunch_end and end > lunch_start:
        return False
    return True


def is_slot_free(start: datetime, end: datetime, busy_ranges: list[dict]) -> bool:
    for busy in busy_ranges:
        busy_start = datetime.fromisoformat(busy["start"].replace("Z", "+00:00")).astimezone(start.tzinfo)
        busy_end = datetime.fromisoformat(busy["end"].replace("Z", "+00:00")).astimezone(start.tzinfo)
        if start < busy_end and end > busy_start:
            return False
    return True
