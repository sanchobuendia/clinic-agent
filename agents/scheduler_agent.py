from services.google_calendar import (
    CalendarIntegrationError,
    GoogleCalendarService,
    normalize_preferred_datetime_text,
)
from state import GraphState, ScheduleResult
from utils.logger import get_logger

logger = get_logger("SchedulerAgent")


async def scheduler_agent(state: GraphState) -> dict:
    decision = state["router_decision"]
    registry = state.get("registry_result")
    action = decision.requested_action if decision else "agendar"
    preferred = decision.preferred_datetime if decision and decision.preferred_datetime else None
    calendar_service = GoogleCalendarService()
    normalized_preferred = normalize_preferred_datetime_text(preferred, calendar_service.timezone) if preferred else None

    if action in {"agendar", "remarcar", "cancelar"}:
        missing_fields: list[str] = []
        if not decision or not decision.patient_cpf:
            missing_fields.append("CPF")
        if not decision or not decision.patient_name:
            missing_fields.append("nome completo")
        if action != "cancelar" and not preferred:
            missing_fields.append("data e horario desejados")

        if missing_fields:
            summary = f"Para {action} sua consulta, preciso de {', '.join(missing_fields)}."
            return {
                "schedule_result": ScheduleResult(
                    action=action,
                    status="awaiting_patient_data",
                    summary=summary,
                    suggested_slots=[],
                )
            }
        if registry and registry.status not in {"found", "created", "ready"}:
            return {
                "schedule_result": ScheduleResult(
                    action=action,
                    status="awaiting_registry",
                    summary="Estou aguardando a validacao do cadastro antes de mexer na agenda.",
                    suggested_slots=[],
                )
            }

    try:
        if not calendar_service.is_configured():
            raise CalendarIntegrationError("Google Calendar nao configurado completamente.")

        if action == "agendar":
            preferred_start, suggested_slots = calendar_service.find_available_slots(normalized_preferred or preferred)
            if preferred and preferred_start is None:
                raise CalendarIntegrationError(
                    f"Nao foi possivel interpretar a data/horario solicitado: {preferred}"
                )
            suggested_slot_labels = [slot.label() for slot in suggested_slots]

            if preferred_start:
                matching_slot = next((slot for slot in suggested_slots if slot.start == preferred_start), None)
                if matching_slot:
                    patient_name = decision.patient_name if decision and decision.patient_name else "Paciente"
                    created_event = calendar_service.create_event(
                        summary=f"Consulta - {patient_name}",
                        start=matching_slot.start,
                        end=matching_slot.end,
                        description=(
                            f"Agendamento criado a partir da solicitacao: {state['user_query']} | "
                            f"CPF: {decision.patient_cpf} | Celular: {decision.patient_phone} | "
                            f"Email: {decision.patient_email}"
                        ),
                    )
                    html_link = created_event.get("htmlLink")
                    summary = f"Consulta agendada para {matching_slot.label()}."
                    if html_link:
                        summary += f" Evento criado no Google Calendar: {html_link}"
                    status = "slot_reserved"
                else:
                    requested_label = normalized_preferred or preferred
                    summary = f"O horario pedido ({requested_label}) nao esta livre. Ofereca uma das opcoes disponiveis."
                    status = "pending_confirmation"
            else:
                summary = "Nao encontrei disponibilidade para o horario solicitado."
                status = "pending_confirmation"

            result = ScheduleResult(
                action=action,
                status=status,
                summary=summary,
                suggested_slots=suggested_slot_labels,
            )
            logger.info("Agenda processada com Google Calendar.")
            return {"schedule_result": result}

        if action == "remarcar":
            event = calendar_service.find_patient_event(
                patient_cpf=decision.patient_cpf if decision else None,
                patient_name=decision.patient_name if decision else None,
            )
            if not event:
                return {
                    "schedule_result": ScheduleResult(
                        action=action,
                        status="pending_confirmation",
                        summary="Nao encontrei consulta futura para remarcacao. Confirme CPF e nome completos.",
                        suggested_slots=[],
                    )
                }

            preferred_start, suggested_slots = calendar_service.find_available_slots(normalized_preferred or preferred)
            if preferred and preferred_start is None:
                raise CalendarIntegrationError(
                    f"Nao foi possivel interpretar a data/horario solicitado: {preferred}"
                )
            suggested_slot_labels = [slot.label() for slot in suggested_slots]
            matching_slot = next((slot for slot in suggested_slots if slot.start == preferred_start), None)
            if not matching_slot:
                requested_label = normalized_preferred or preferred
                return {
                    "schedule_result": ScheduleResult(
                        action=action,
                        status="pending_confirmation",
                        summary=(
                            f"Nao encontrei vaga no horario pedido ({requested_label}). "
                            "Ofereca uma das opcoes disponiveis para remarcacao."
                        ),
                        suggested_slots=suggested_slot_labels,
                    )
                }

            updated_event = calendar_service.reschedule_event(
                event_id=event["id"],
                new_start=matching_slot.start,
                new_end=matching_slot.end,
            )
            html_link = updated_event.get("htmlLink")
            summary = f"Consulta remarcada para {matching_slot.label()}."
            if html_link:
                summary += f" Evento atualizado: {html_link}"
            return {
                "schedule_result": ScheduleResult(
                    action=action,
                    status="slot_reserved",
                    summary=summary,
                    suggested_slots=suggested_slot_labels,
                )
            }

        if action == "cancelar":
            event = calendar_service.find_patient_event(
                patient_cpf=decision.patient_cpf if decision else None,
                patient_name=decision.patient_name if decision else None,
                preferred_text=normalized_preferred or preferred,
            )
            if not event:
                return {
                    "schedule_result": ScheduleResult(
                        action=action,
                        status="pending_confirmation",
                        summary="Nao encontrei consulta futura para cancelamento. Confirme CPF e nome completos.",
                        suggested_slots=[],
                    )
                }

            calendar_service.cancel_event(event_id=event["id"])
            start_dt = event.get("start", {}).get("dateTime", "horario desconhecido")
            summary = f"Consulta cancelada com sucesso. Horario anterior: {start_dt}."
            return {
                "schedule_result": ScheduleResult(
                    action=action,
                    status="cancelled",
                    summary=summary,
                    suggested_slots=[],
                )
            }

        summary = "Demanda de agenda identificada. Consulte os horarios sugeridos antes de confirmar."
        result = ScheduleResult(
            action=action,
            status="needs_review",
            summary=summary,
            suggested_slots=[],
        )
        return {"schedule_result": result}
    except CalendarIntegrationError as exc:
        logger.warning("Integracao com Google Calendar indisponivel: %s", exc)
        error_message = f"Google Calendar indisponivel: {exc}"
        return {
            "schedule_result": ScheduleResult(
                action=action,
                status="integration_error",
                summary=error_message,
                suggested_slots=[],
            ),
            "error_log": state.get("error_log", []) + [error_message],
        }
    except Exception as exc:  # pragma: no cover - external API/runtime failures
        logger.exception("Falha ao consultar Google Calendar: %s", exc)
        error_message = f"Falha ao consultar Google Calendar: {exc}"
        return {
            "schedule_result": ScheduleResult(
                action=action,
                status="integration_error",
                summary=error_message,
                suggested_slots=[],
            ),
            "error_log": state.get("error_log", []) + [error_message],
        }
