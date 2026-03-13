import uuid
from contextlib import asynccontextmanager
from datetime import datetime
import re

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from psycopg import OperationalError
from pydantic import BaseModel, Field

import graph
from services.patient_registry import PatientRegistryError, get_patient_by_cpf, list_patients
from services.email_service import EmailServiceError, send_email
from services.google_calendar import CalendarIntegrationError, GoogleCalendarService
from state import GraphState
from utils.logger import get_logger

load_dotenv()
logger = get_logger("API")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await graph.setup_checkpointer()
    try:
        yield
    finally:
        await graph.shutdown_checkpointer()


app = FastAPI(
    title="Clinic Office Agent API",
    version="1.0.0",
    description="API multiagente para atendimento administrativo de consultorios",
    lifespan=lifespan,
)


class QueryRequest(BaseModel):
    query: str = Field(
        default="Gostaria de agendar uma consulta para o dia 25/03/2026 as 9h. Meu nome é Aureliano Sancho, masculino, 41 anos, CPF 01929820526, telefone 992391210, email sanchobuendia@gmail.com",
        description="Mensagem administrativa do paciente",
    )
    thread_id: str | None = Field(
        default=f"clinic-thread-{datetime.now().strftime('%Y%m%d')}-100",
        description="ID da conversa",
    )


class QueryResponse(BaseModel):
    thread_id: str
    response: str
    cache_hit: bool
    fallback_triggered: bool
    error_count: int


class PatientResponse(BaseModel):
    id: int
    cpf: str
    full_name: str
    age: int
    sex: str
    email: str
    phone: str


class ReminderDispatchResponse(BaseModel):
    reminders_sent: int
    no_show_checks_sent: int
    inspected_events: int


def _is_retryable_checkpoint_error(exc: Exception) -> bool:
    message = str(exc).lower()
    retry_markers = [
        "the connection is closed",
        "could not receive data from server",
        "ssl syscall error",
        "operation timed out",
        "connection timeout",
    ]
    return isinstance(exc, OperationalError) or any(marker in message for marker in retry_markers)


async def _rebuild_graph() -> None:
    await graph.shutdown_checkpointer()
    await graph.setup_checkpointer()


def _merge_follow_up_query(query: str, previous_values: dict | None) -> str:
    if not previous_values:
        return query

    previous_schedule = previous_values.get("schedule_result")
    previous_decision = previous_values.get("router_decision")
    previous_query = previous_values.get("user_query")

    pending_statuses = {"awaiting_patient_data", "awaiting_cpf", "awaiting_registry"}
    if previous_decision and getattr(previous_decision, "requested_action", None) == "agendar" and previous_query:
        if previous_schedule and getattr(previous_schedule, "status", None) in pending_statuses:
            return f"{previous_query}\n{query}"
        previous_registry = previous_values.get("registry_result")
        if previous_registry and getattr(previous_registry, "status", None) in pending_statuses:
            return f"{previous_query}\n{query}"
        previous_telemedicine = previous_values.get("telemedicine_result")
        if previous_telemedicine and getattr(previous_telemedicine, "status", None) == "needs_more_info":
            return f"{previous_query}\n{query}"

    return query


async def run_query(query: str, thread_id: str | None = None) -> dict:
    thread_id = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    logger.debug("Paciente [%s]: %s", thread_id, query)

    previous_values: dict | None = None
    try:
        snapshot = await graph.get_compiled_graph().aget_state(config)
        previous_values = snapshot.values if snapshot else None
    except Exception as exc:
        logger.warning("Nao foi possivel recuperar estado anterior da thread %s: %s", thread_id, exc)
        if _is_retryable_checkpoint_error(exc):
            logger.warning("Reconstruindo grafo apos falha de checkpointer ao ler estado.")
            try:
                await _rebuild_graph()
                snapshot = await graph.get_compiled_graph().aget_state(config)
                previous_values = snapshot.values if snapshot else None
            except Exception as retry_exc:
                logger.warning("Falha ao recuperar estado mesmo apos reconstruir o grafo: %s", retry_exc)

    effective_query = _merge_follow_up_query(query, previous_values)
    initial_state: GraphState = {
        "messages": [],
        "user_query": effective_query,
        "router_decision": previous_values.get("router_decision") if previous_values else None,
        "schedule_result": previous_values.get("schedule_result") if previous_values else None,
        "registry_result": previous_values.get("registry_result") if previous_values else None,
        "telemedicine_result": previous_values.get("telemedicine_result") if previous_values else None,
        "notification_result": previous_values.get("notification_result") if previous_values else None,
        "final_response": None,
        "retry_count": 0,
        "error_log": previous_values.get("error_log", []) if previous_values else [],
        "fallback_triggered": False,
        "human_approved": None,
        "cache_hit": False,
    }

    try:
        final_state = await graph.get_compiled_graph().ainvoke(initial_state, config=config)
    except Exception as exc:
        if _is_retryable_checkpoint_error(exc):
            logger.warning("Reconstruindo grafo apos falha de checkpointer durante a execucao.")
            try:
                await _rebuild_graph()
                final_state = await graph.get_compiled_graph().ainvoke(initial_state, config=config)
            except Exception as retry_exc:
                logger.error("Erro critico no grafo apos retry.", exc_info=True)
                raise HTTPException(status_code=500, detail=f"Erro interno: {retry_exc}") from retry_exc
        else:
            logger.error("Erro critico no grafo.", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Erro interno: {exc}") from exc

    result = {
        "thread_id": thread_id,
        "response": final_state.get("final_response", "Sem resposta gerada."),
        "cache_hit": final_state.get("cache_hit", False),
        "fallback_triggered": final_state.get("fallback_triggered", False),
        "error_count": len(final_state.get("error_log", [])),
    }
    logger.debug("Assistente [%s]: %s", thread_id, result["response"])
    return result


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/patients", response_model=list[PatientResponse])
async def list_patients_endpoint(limit: int = 50):
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="O parametro limit deve ficar entre 1 e 200.")
    try:
        patients = await list_patients(limit=limit)
    except PatientRegistryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return [PatientResponse(**patient.__dict__) for patient in patients]


@app.get("/patients/{cpf}", response_model=PatientResponse)
async def get_patient_endpoint(cpf: str):
    normalized_cpf = "".join(char for char in cpf if char.isdigit())
    if len(normalized_cpf) != 11:
        raise HTTPException(status_code=400, detail="CPF invalido. Envie 11 digitos.")
    try:
        patient = await get_patient_by_cpf(normalized_cpf)
    except PatientRegistryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not patient:
        raise HTTPException(status_code=404, detail="Paciente nao encontrado.")
    return PatientResponse(**patient.__dict__)


@app.post("/query", response_model=QueryResponse)
async def query_endpoint(payload: QueryRequest):
    if not payload.query.strip():
        raise HTTPException(status_code=400, detail="A query nao pode estar vazia.")
    result = await run_query(payload.query, payload.thread_id)
    return QueryResponse(**result)


def _extract_email_from_event(event: dict) -> str | None:
    description = event.get("description") or ""
    match = re.search(
        r"email\s*:\s*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})",
        description,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower()


def _extract_patient_name_from_event(event: dict) -> str:
    summary = event.get("summary") or "Consulta"
    if "-" in summary:
        return summary.split("-", maxsplit=1)[1].strip()
    return summary


@app.post("/appointments/reminders", response_model=ReminderDispatchResponse)
async def dispatch_appointment_reminders():
    calendar_service = GoogleCalendarService()
    if not calendar_service.is_configured():
        raise HTTPException(status_code=500, detail="Google Calendar nao configurado completamente.")

    try:
        events = calendar_service.list_upcoming_events(days_ahead=2, max_results=200)
    except CalendarIntegrationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    now = datetime.now(calendar_service.timezone)
    reminders_sent = 0
    no_show_checks_sent = 0

    for event in events:
        start_value = event.get("start", {}).get("dateTime")
        end_value = event.get("end", {}).get("dateTime")
        if not start_value:
            continue
        try:
            start_dt = datetime.fromisoformat(start_value).astimezone(calendar_service.timezone)
            end_dt = datetime.fromisoformat(end_value).astimezone(calendar_service.timezone) if end_value else None
        except ValueError:
            logger.warning("Evento com formato de data invalido foi ignorado: %s", event.get("id"))
            continue
        delta_minutes = int((start_dt - now).total_seconds() / 60)

        patient_email = _extract_email_from_event(event)
        if not patient_email:
            continue
        patient_name = _extract_patient_name_from_event(event)

        try:
            if 110 <= delta_minutes <= 130:
                send_email(
                    to_email=patient_email,
                    subject="Lembrete: sua consulta e em ~2 horas",
                    body=(
                        f"Ola, {patient_name}.\n\n"
                        f"Este e um lembrete da sua consulta marcada para {start_dt.strftime('%d/%m/%Y %H:%M')}."
                    ),
                )
                reminders_sent += 1
            elif 23 * 60 + 45 <= delta_minutes <= 24 * 60 + 15:
                send_email(
                    to_email=patient_email,
                    subject="Lembrete: sua consulta e amanha",
                    body=(
                        f"Ola, {patient_name}.\n\n"
                        f"Sua consulta esta confirmada para {start_dt.strftime('%d/%m/%Y %H:%M')}."
                    ),
                )
                reminders_sent += 1
            elif end_dt and -90 <= int((end_dt - now).total_seconds() / 60) <= -30:
                send_email(
                    to_email=patient_email,
                    subject="Confirmacao de presenca",
                    body=(
                        f"Ola, {patient_name}.\n\n"
                        "Nao identificamos seu check-in automaticamente. "
                        "Se voce compareceu, desconsidere. Caso contrario, responda para ajudarmos no reagendamento."
                    ),
                )
                no_show_checks_sent += 1
        except EmailServiceError as exc:
            logger.warning("Falha ao enviar email de lembrete para %s: %s", patient_email, exc)
        except Exception as exc:  # pragma: no cover - falhas de rede/SMTP em runtime
            logger.exception("Erro inesperado ao enviar email de lembrete para %s: %s", patient_email, exc)

    return ReminderDispatchResponse(
        reminders_sent=reminders_sent,
        no_show_checks_sent=no_show_checks_sent,
        inspected_events=len(events),
    )
