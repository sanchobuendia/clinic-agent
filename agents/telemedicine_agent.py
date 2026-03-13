from langchain_core.prompts import ChatPromptTemplate

from services.knowledge_base import build_rag_context
from state import GraphState, TelemedicineResult
from utils.logger import get_logger
from utils.llm import model_aws
from .prompts.telemedicine_system_prompt import TELEMEDICINE_SYSTEM_PROMPT

logger = get_logger("TelemedicineAgent")

DERM_COMPLAINT_KEYWORDS = [
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
    "vermelhid",
    "prurido",
    "ardor",
    "caspa",
    "unha",
    "couro cabeludo",
]


def _has_dermatology_complaint(query: str) -> bool:
    lowered = query.lower()
    return any(keyword in lowered for keyword in DERM_COMPLAINT_KEYWORDS)


async def telemedicine_agent(state: GraphState) -> dict:
    query = state["user_query"]
    decision = state.get("router_decision")

    if decision and decision.requested_action == "agendar" and not _has_dermatology_complaint(query):
        result = TelemedicineResult(
            status="needs_more_info",
            summary="Antes de marcar sua consulta, preciso entender qual e a queixa dermatologica principal.",
            guidance=(
                "Por favor, descreva o problema de pele que motivou o atendimento, "
                "incluindo local do corpo, tempo de evolucao e sintomas como coceira, dor, descamacao ou secrecao."
            ),
            recommended_next_step=(
                "Assim que eu entender a queixa, tento orientar melhor. "
                "Se precisar de mais alguma ajuda, posso continuar com a teleorientacao. "
                "Se depois disso voce preferir, tambem posso prosseguir para o agendamento."
            ),
            requires_appointment=False,
            references=[],
            queries_used=[],
        )
        logger.info("Telemedicina solicitou descricao da queixa antes do agendamento.")
        return {"telemedicine_result": result}

    try:
        rag_payload = build_rag_context(query, limit=5, limit_per_query=4)
    except Exception as exc:
        logger.warning("Falha ao consultar a base dermatologica: %s", exc)
        result = TelemedicineResult(
            status="needs_more_info",
            summary="A base dermatologica nao esta disponivel no momento.",
            guidance="Posso seguir com uma orientacao mais limitada ou encaminhar para consulta dermatologica.",
            recommended_next_step=(
                "Se precisar de mais alguma ajuda, posso continuar com orientacoes gerais. "
                "Se preferir, tambem posso prosseguir para o agendamento da consulta."
            ),
            requires_appointment=True,
            references=[],
            queries_used=[],
        )
        return {"telemedicine_result": result, "error_log": state.get("error_log", []) + [str(exc)]}

    context = rag_payload["context"]
    queries_used = rag_payload["queries"]
    matches = rag_payload["matches"]

    if not context:
        result = TelemedicineResult(
            status="needs_more_info",
            summary="Nao encontrei contexto suficiente na base dermatologica para orientar com seguranca.",
            guidance="Preciso de mais detalhes sobre a lesao, sintomas, tempo de evolucao e local do corpo afetado.",
            recommended_next_step=(
                "Se houver piora rapida, dor intensa, febre ou sinais extensos na pele, procure avaliacao medica. "
                "Se quiser, descreva melhor os sintomas ou me diga se prefere prosseguir para o agendamento."
            ),
            requires_appointment=True,
            references=[],
            queries_used=queries_used,
        )
        logger.warning("Base dermatologica sem resultados para a pergunta atual.")
        return {"telemedicine_result": result}

    try:
        llm = model_aws()
        structured_llm = llm.with_structured_output(TelemedicineResult)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", TELEMEDICINE_SYSTEM_PROMPT),
                (
                    "user",
                    "Pergunta do paciente:\n{query}\n\n"
                    "Contexto recuperado:\n{context}\n\n"
                    "Responda com base apenas nesse contexto.",
                ),
            ]
        )
        chain = prompt | structured_llm
        result: TelemedicineResult = await chain.ainvoke({"query": query, "context": context})
        result.queries_used = queries_used
        result.references = []
        if result.requires_appointment and result.status == "answered":
            result.status = "awaiting_schedule_confirmation"
        if "mais alguma ajuda" not in result.recommended_next_step.lower() and "agendamento" not in result.recommended_next_step.lower():
            result.recommended_next_step = (
                f"{result.recommended_next_step} "
                "Se precisar de mais alguma ajuda, posso continuar te orientando. "
                "Se preferir, tambem posso prosseguir para o agendamento."
            ).strip()
    except Exception as exc:
        logger.warning("Falha no telemedicine_agent por LLM. Usando fallback local: %s", exc)
        result = TelemedicineResult(
            status="awaiting_schedule_confirmation",
            summary="Encontrei informacoes relevantes na base dermatologica para orientar a conduta inicial.",
            guidance=context.split("\n\n", 1)[0][:1200],
            recommended_next_step=(
                "Se os sintomas persistirem, piorarem ou houver duvida diagnostica, vale agendar consulta com dermatologia. "
                "Se precisar de mais alguma ajuda, posso continuar te orientando. "
                "Se preferir, tambem posso prosseguir para o agendamento."
            ),
            requires_appointment=True,
            references=[],
            queries_used=queries_used,
        )

    logger.info("Teleorientacao dermatologica concluida com %s referencias.", len(result.references))
    return {"telemedicine_result": result}
