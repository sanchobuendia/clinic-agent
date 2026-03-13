import re

from langchain_core.messages import HumanMessage

from guardrails.guardrails import input_guardrail
from state import GraphState, RouterDecision
from utils.logger import get_logger

logger = get_logger("RootAgent")


def _is_greeting(query: str) -> bool:
    lowered = query.strip().lower()
    greeting_patterns = [
        r"^oi+$",
        r"^ola+$",
        r"^olá+$",
        r"^bom dia$",
        r"^boa tarde$",
        r"^boa noite$",
        r"^(oi|ola|olá)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in greeting_patterns)


def _has_schedule_confirmation(query: str) -> bool:
    lowered = query.strip().lower()
    confirmation_patterns = [
        r"\b(pode|pode sim|sim pode)\s+(agendar|marcar)\b",
        r"\bquero\s+(agendar|marcar)\b",
        r"\bpode\s+prosseguir\s+com\s+o\s+agendamento\b",
        r"\bprosseguir\s+com\s+o\s+agendamento\b",
        r"\bseguir\s+com\s+o\s+agendamento\b",
        r"\bsim\b.*\b(agendar|marcar)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in confirmation_patterns)


def _extract_datetime_hint(query: str) -> str | None:
    patterns = [
        r"\b(segunda|terca|quarta|quinta|sexta|sabado|domingo)\b(?:\s+a\s+\w+)?(?:\s+(de manha|a tarde|a noite))?",
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b(?:\s+(?:as|às)?\s*\d{1,2}(?:[:h]\d{2})?(?:\s*horas?)?)?",
        r"\bamanha\b(?:\s+de manha|\s+a tarde|\s+a noite|\s+(?:as|às)?\s*\d{1,2}(?:[:h]\d{2})?(?:\s*horas?)?)?",
    ]
    lowered = query.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0)
    return None


def _extract_time_hint(query: str) -> str | None:
    patterns = [
        r"\b(?:as|às)\s*\d{1,2}(?:[:h]\d{2})?(?:\s*horas?)?\b",
        r"\b\d{1,2}:\d{2}\b",
        r"\b\d{1,2}h\d{0,2}\b",
        r"\b\d{1,2}\s*horas?\b",
    ]
    lowered = query.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0).strip()
    return None


def _merge_date_and_time(previous_datetime: str | None, new_query: str) -> str | None:
    if not previous_datetime:
        return None

    time_hint = _extract_time_hint(new_query)
    if not time_hint:
        return None

    date_hint_match = re.search(
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\bamanha\b|\bamanhã\b|\bsegunda\b|\bterca\b|\bterça\b|\bquarta\b|\bquinta\b|\bsexta\b|\bsabado\b|\bsábado\b|\bdomingo\b",
        previous_datetime.lower(),
    )
    if not date_hint_match:
        return None

    return f"{date_hint_match.group(0)} {time_hint}"


def _extract_patient_name(query: str) -> str | None:
    patterns = [
        r"\bmeu nome e\s+([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){0,3})",
        r"\bme chamo\s+([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){0,3})",
        r"\baqui e\s+([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){0,3})",
        r"\bsou o?\s*([A-Za-zÀ-ÿ]+(?:\s+[A-Za-zÀ-ÿ]+){0,3})",
    ]
    lowered = query.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(1).strip().title()
    return None


def _extract_email(query: str) -> str | None:
    match = re.search(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", query)
    if not match:
        return None
    return match.group(0).lower()


def _extract_age(query: str) -> int | None:
    patterns = [
        r"\bidade[:\s]+(\d{1,3})\b",
        r"\btenho\s+(\d{1,3})\s+anos\b",
        r"\b(\d{1,3})\s+anos\b",
    ]
    lowered = query.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            age = int(match.group(1))
            if 0 < age < 130:
                return age
    return None


def _extract_sex(query: str) -> str | None:
    lowered = query.lower()
    patterns = {
        r"\bsexo[:\s]+(masculino|feminino)\b": None,
        r"\bsexo[:\s]+(m|f)\b": None,
        r"\b(masculino|feminino)\b": None,
    }
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            value = match.group(1)
            if value == "m":
                return "masculino"
            if value == "f":
                return "feminino"
            return value
    return None


def _extract_phone(query: str) -> str | None:
    lowered = query.lower()
    label_match = re.search(
        r"\b(?:telefone|celular|fone)\s*(?:e|é|:)?\s*((?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?(?:9?\d{4})-?\d{4})",
        lowered,
    )
    if label_match:
        digits = re.sub(r"\D", "", label_match.group(1))
        if len(digits) in {10, 11, 12, 13}:
            return digits

    formatted_match = re.search(r"\(\d{2}\)\s*9?\d{4}-?\d{4}\b", query)
    if not formatted_match:
        return None
    digits = re.sub(r"\D", "", formatted_match.group(0))
    return digits if len(digits) in {10, 11} else None


def _extract_cpf(query: str) -> str | None:
    lowered = query.lower()
    label_match = re.search(r"\bcpf\s*(?:e|é|:)?\s*(\d{3}\.?\d{3}\.?\d{3}-?\d{2})\b", lowered)
    candidate = label_match.group(1) if label_match else None
    if candidate is None:
        punctuated_match = re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", query)
        if punctuated_match:
            candidate = punctuated_match.group(0)
    if not candidate:
        return None
    normalized = re.sub(r"\D", "", candidate)
    return normalized if _is_valid_cpf(normalized) else None


def _is_valid_cpf(cpf: str) -> bool:
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False

    total = sum(int(cpf[index]) * (10 - index) for index in range(9))
    digit = (total * 10) % 11
    digit = 0 if digit == 10 else digit
    if digit != int(cpf[9]):
        return False

    total = sum(int(cpf[index]) * (11 - index) for index in range(10))
    digit = (total * 10) % 11
    digit = 0 if digit == 10 else digit
    return digit == int(cpf[10])


def _rule_based_decision(query: str) -> RouterDecision:
    lowered = query.lower()

    schedule_keywords = ["agendar", "consulta", "remarcar", "cancelar", "encaixe", "horario"]
    telemedicine_keywords = [
        "pele",
        "mancha",
        "coceira",
        "coçar",
        "alergia",
        "acne",
        "espinha",
        "lesao",
        "lesão",
        "micose",
        "queda de cabelo",
        "verruga",
        "pinta",
        "rash",
        "descamacao",
        "descamação",
        "ferida",
        "dermat",
    ]
    registry_keywords = ["cadastro", "documento", "dados", "telefone", "convenio", "ficha"]
    notification_keywords = ["lembr", "confirm", "avis", "notific"]

    requested_action = "orientar"
    if "remarcar" in lowered:
        requested_action = "remarcar"
    elif "cancel" in lowered:
        requested_action = "cancelar"
    elif "agendar" in lowered or "encaixe" in lowered:
        requested_action = "agendar"
    elif any(word in lowered for word in registry_keywords):
        requested_action = "cadastro"
    elif _is_greeting(query):
        requested_action = "atendimento_geral"

    return RouterDecision(
        needs_scheduler=any(word in lowered for word in schedule_keywords),
        needs_registry=any(word in lowered for word in registry_keywords),
        needs_telemedicine=any(word in lowered for word in telemedicine_keywords) or "consulta" in lowered,
        needs_notification=any(word in lowered for word in notification_keywords) or "agendar" in lowered,
        intent=requested_action,
        requested_action=requested_action,
        urgency_level="alta" if "urgente" in lowered else "normal",
        patient_cpf=_extract_cpf(query),
        patient_name=_extract_patient_name(query),
        patient_age=_extract_age(query),
        patient_sex=_extract_sex(query),
        patient_email=_extract_email(query),
        patient_phone=_extract_phone(query),
        preferred_datetime=_extract_datetime_hint(query),
        summary=query.strip(),
    )


async def root_agent(state: GraphState) -> dict:
    query = state["user_query"]
    previous_decision = state.get("router_decision")
    previous_registry = state.get("registry_result")
    previous_telemedicine = state.get("telemedicine_result")
    logger.info("Processando triagem administrativa.")

    guard_result = await input_guardrail(query)
    if not guard_result.passed:
        return {
            "router_decision": RouterDecision(
                needs_scheduler=False,
                needs_registry=False,
                needs_telemedicine=False,
                needs_notification=False,
                intent="blocked",
                requested_action="blocked",
                guardrail_passed=False,
                rejection_reason=guard_result.reason,
            ),
            "messages": [HumanMessage(content=query)],
        }

    fallback_triggered = False
    decision = _rule_based_decision(query)
    decision.guardrail_passed = True
    decision.rejection_reason = None

    extracted_datetime = _extract_datetime_hint(query)
    if not extracted_datetime and previous_decision:
        extracted_datetime = _merge_date_and_time(previous_decision.preferred_datetime, query)
    if extracted_datetime:
        decision.preferred_datetime = extracted_datetime

    logger.info(
        "Triagem deterministica concluida: agenda=%s cadastro=%s telemed=%s notificacao=%s acao=%s",
        decision.needs_scheduler,
        decision.needs_registry,
        decision.needs_telemedicine,
        decision.needs_notification,
        decision.requested_action,
    )

    if not any(
        [
            decision.needs_scheduler,
            decision.needs_registry,
            decision.needs_telemedicine,
            decision.needs_notification,
        ]
    ):
        decision.intent = "atendimento_geral"
        decision.requested_action = "atendimento_geral"
        if not decision.summary:
            decision.summary = query.strip()

    if previous_decision and previous_decision.requested_action == "agendar":
        decision.requested_action = "agendar"
        decision.intent = "agendar"
        decision.needs_registry = True
        decision.needs_telemedicine = True
        decision.needs_scheduler = False
        decision.needs_notification = False
        decision.patient_cpf = decision.patient_cpf or previous_decision.patient_cpf
        decision.patient_name = decision.patient_name or previous_decision.patient_name
        decision.patient_age = decision.patient_age or previous_decision.patient_age
        decision.patient_sex = decision.patient_sex or previous_decision.patient_sex
        decision.patient_email = decision.patient_email or previous_decision.patient_email
        decision.patient_phone = decision.patient_phone or previous_decision.patient_phone
        if not decision.preferred_datetime:
            decision.preferred_datetime = _merge_date_and_time(previous_decision.preferred_datetime, query)
        decision.preferred_datetime = decision.preferred_datetime or previous_decision.preferred_datetime

    if previous_registry and previous_registry.status in {"found", "created", "ready"}:
        decision.needs_registry = False

    if previous_telemedicine and previous_telemedicine.status == "needs_more_info":
        decision.requested_action = "agendar"
        decision.intent = "agendar"
        decision.needs_telemedicine = True
        decision.needs_scheduler = False
        decision.needs_notification = False

    if previous_telemedicine and previous_telemedicine.requires_appointment and decision.requested_action == "agendar":
        if _has_schedule_confirmation(query):
            decision.needs_registry = False if previous_registry and previous_registry.status in {"found", "created", "ready"} else True
            decision.needs_telemedicine = False
            decision.needs_scheduler = True
            decision.needs_notification = True
        else:
            decision.needs_registry = False if previous_registry and previous_registry.status in {"found", "created", "ready"} else True
            decision.needs_telemedicine = True
            decision.needs_scheduler = False
            decision.needs_notification = False

    if decision.needs_telemedicine and (not previous_registry or previous_registry.status not in {"found", "created", "ready"}):
        decision.needs_registry = True

    if decision.requested_action == "agendar":
        if not previous_registry or previous_registry.status not in {"found", "created", "ready"}:
            decision.needs_registry = True
        if previous_telemedicine and previous_telemedicine.requires_appointment:
            if _has_schedule_confirmation(query):
                decision.needs_telemedicine = False
                decision.needs_scheduler = True
                decision.needs_notification = True
            else:
                decision.needs_telemedicine = True
                decision.needs_scheduler = False
                decision.needs_notification = False
        else:
            decision.needs_telemedicine = True
            decision.needs_scheduler = False

    if previous_decision and previous_decision.preferred_datetime and _extract_time_hint(query):
        merged_datetime = _merge_date_and_time(previous_decision.preferred_datetime, query)
        if merged_datetime:
            decision.preferred_datetime = merged_datetime

    return {
        "router_decision": decision,
        "messages": [HumanMessage(content=query)],
        "retry_count": 0,
        "error_log": [],
        "fallback_triggered": fallback_triggered,
        "cache_hit": False,
    }
