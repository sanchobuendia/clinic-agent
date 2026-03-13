import re

from pydantic import BaseModel

from utils.logger import get_logger

logger = get_logger("Guardrails")


class GuardrailResult(BaseModel):
    passed: bool
    reason: str | None = None
    sanitized_input: str | None = None


BLOCKED_PATTERNS = [
    r"\b(password|senha|sql injection|xss|exploit)\b",
    r"\b(cartao de credito|credit card|rg)\b",
]

ADMIN_KEYWORDS = [
    "consulta",
    "agendar",
    "remarcar",
    "cancelar",
    "encaixe",
    "horario",
    "pagamento",
    "recibo",
    "secretaria",
    "paciente",
    "cadastro",
    "documento",
    "convenio",
    "lembrete",
    "confirmacao",
    "atendimento",
    "doutor",
    "dentista",
    "medico",
    "clinica",
]

GREETING_PATTERNS = [
    r"^oi+$",
    r"^ola+$",
    r"^olá+$",
    r"^bom dia$",
    r"^boa tarde$",
    r"^boa noite$",
    r"^(oi|ola|olá)\b",
]

CLINICAL_EMERGENCY_PATTERNS = [
    r"\bdor no peito\b",
    r"\bfalta de ar\b",
    r"\bsangramento intenso\b",
    r"\bdesmaio\b",
    r"\burgencia\b",
]


async def input_guardrail(query: str) -> GuardrailResult:
    text = query.strip()
    if not text:
        return GuardrailResult(passed=False, reason="A mensagem nao pode estar vazia.")

    if len(text) > 1000:
        return GuardrailResult(passed=False, reason="Mensagem muito longa. Envie um pedido mais objetivo.")

    lowered = text.lower()
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, lowered):
            logger.warning("Input bloqueado por padrao sensivel.")
            return GuardrailResult(passed=False, reason="Sua mensagem contem conteudo nao permitido.")

    if any(re.search(pattern, lowered) for pattern in CLINICAL_EMERGENCY_PATTERNS):
        return GuardrailResult(
            passed=False,
            reason="Este canal cuida apenas de atendimento administrativo. Em caso de urgencia, procure atendimento medico imediato.",
        )

    return GuardrailResult(passed=True, sanitized_input=text)


async def output_guardrail(response: str) -> GuardrailResult:
    if len(response.strip()) < 30:
        return GuardrailResult(passed=False, reason="Resposta curta demais para entrega.")

    if re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", response):
        return GuardrailResult(passed=False, reason="A resposta contem dado sensivel.")

    return GuardrailResult(passed=True)
