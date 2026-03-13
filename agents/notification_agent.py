from services.email_service import EmailServiceError, send_email
from state import GraphState, NotificationResult
from utils.logger import get_logger

logger = get_logger("NotificationAgent")


async def notification_agent(state: GraphState) -> dict:
    decision = state["router_decision"]
    registry = state.get("registry_result")
    schedule = state.get("schedule_result")
    telemedicine = state.get("telemedicine_result")
    channel = decision.notify_channel if decision else "whatsapp"
    preview = "Sua solicitacao foi registrada. Em breve enviaremos a confirmacao do horario."
    status = "queued"

    if decision and decision.requested_action == "cancelar":
        if schedule and schedule.status == "cancelled":
            preview = "Cancelamento concluido. Enviamos a confirmacao para o paciente."
        else:
            preview = "Recebemos o pedido de cancelamento. Aguarde a confirmacao da liberacao do horario."
    elif decision and decision.requested_action == "remarcar":
        if schedule and schedule.status == "slot_reserved":
            preview = "Remarcacao concluida. Enviamos os novos dados ao paciente."
        else:
            preview = "Estamos verificando novos horarios para remarcacao e retornaremos com opcoes."
    elif schedule and schedule.status == "slot_reserved":
        patient_name = registry.patient_name if registry and registry.patient_name else "Paciente"
        patient_email = registry.patient_email if registry and registry.patient_email else (
            decision.patient_email if decision else None
        )
        preview = "Consulta agendada. Resumo e detalhes enviados por email ao paciente."
        channel = "email"

        if patient_email:
            interaction_summary = []
            if telemedicine and telemedicine.summary:
                interaction_summary.append(telemedicine.summary)
            if telemedicine and telemedicine.guidance:
                interaction_summary.append(telemedicine.guidance)
            if not interaction_summary:
                interaction_summary.append("Atendimento administrativo concluido com agendamento confirmado.")

            body_parts = [
                f"Olá, {patient_name}.",
                "",
                "Sua consulta foi agendada com sucesso.",
                "Dados do agendamento:",
                schedule.summary,
            ]
            if schedule.suggested_slots:
                body_parts.extend(
                    [
                        "",
                        f"Outros horarios que estavam disponiveis no momento da busca: {', '.join(schedule.suggested_slots)}.",
                    ]
                )
            body_parts.extend(
                [
                    "",
                    "Resumo da interacao:",
                    *interaction_summary,
                ]
            )
            if decision and decision.patient_phone:
                body_parts.extend(
                    [
                        "",
                        f"Celular informado: {decision.patient_phone}",
                    ]
                )
            if decision and decision.patient_email:
                body_parts.append(f"Email informado: {decision.patient_email}")
            body_parts.extend(
                [
                    "",
                    "Mensagem gerada automaticamente pelo assistente do consultorio de dermatologia.",
                ]
            )
            try:
                send_email(
                    to_email=patient_email,
                    subject="Resumo do atendimento e confirmacao de agendamento",
                    body="\n".join(body_parts),
                )
                status = "sent"
            except EmailServiceError as exc:
                logger.warning("Envio de email indisponivel: %s", exc)
                preview = "Consulta agendada. Falha ao enviar email de confirmacao."
                status = "email_unavailable"
            except Exception as exc:  # pragma: no cover - runtime SMTP/network failures
                logger.exception("Falha ao enviar email de confirmacao: %s", exc)
                preview = "Consulta agendada. Falha ao enviar email de confirmacao."
                status = "email_error"
        else:
            preview = "Consulta agendada. Nao encontrei email valido para enviar o resumo."
            status = "missing_email"

    result = NotificationResult(
        status=status,
        channel=channel,
        message_preview=preview,
    )
    logger.info("Notificacao preparada.")
    return {"notification_result": result}
