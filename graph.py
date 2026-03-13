import os
from contextlib import AbstractAsyncContextManager

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from agents.notification_agent import notification_agent
from agents.registry_agent import registry_agent
from agents.root_agent import root_agent
from agents.scheduler_agent import scheduler_agent
from agents.telemedicine_agent import telemedicine_agent
from services.patient_registry import PatientRegistryError, setup_patient_registry
from state import GraphState, RouterDecision
from utils.logger import get_logger

load_dotenv()

logger = get_logger("Graph")
compiled_graph = None
_checkpointer_cm: AbstractAsyncContextManager[AsyncPostgresSaver] | None = None


async def node_root(state: GraphState) -> dict:
    return await root_agent(state)


async def node_scheduler(state: GraphState) -> dict:
    return await scheduler_agent(state)


async def node_registry(state: GraphState) -> dict:
    return await registry_agent(state)


async def node_telemedicine(state: GraphState) -> dict:
    return await telemedicine_agent(state)


async def node_notification(state: GraphState) -> dict:
    return await notification_agent(state)


def node_finalize_response(state: GraphState) -> dict:
    schedule = state.get("schedule_result")
    registry = state.get("registry_result")
    telemedicine = state.get("telemedicine_result")
    notification = state.get("notification_result")
    decision: RouterDecision | None = state.get("router_decision")

    parts: list[str] = []
    if registry and registry.status not in {"found", "created", "ready"}:
        parts.append(registry.summary)
    if schedule:
        parts.append(schedule.summary)
        if schedule.suggested_slots:
            parts.append(f"Horarios sugeridos: {', '.join(schedule.suggested_slots)}.")
    elif telemedicine:
        parts.append(telemedicine.summary)
        parts.append(telemedicine.guidance)
        parts.append(telemedicine.recommended_next_step)
    if notification and schedule and schedule.status == "slot_reserved":
        parts.append(notification.message_preview)

    if not parts:
        if decision and decision.requested_action == "atendimento_geral":
            final_response = (
                "Olá! Posso ajudar com agendamento, remarcação, cancelamento, cadastro ou pagamentos."
            )
        else:
            final_response = "Solicitação recebida."
    else:
        final_response = "\n\n".join(parts)

    return {
        "final_response": final_response,
    }


def node_blocked_response(state: GraphState) -> dict:
    decision: RouterDecision | None = state.get("router_decision")
    reason = decision.rejection_reason if decision else "Solicitacao nao permitida."
    return {
        "final_response": reason,
        "messages": [AIMessage(content=reason, name="guardrail_block")],
    }


def route_after_root(state: GraphState):
    decision: RouterDecision | None = state.get("router_decision")
    if not decision or not decision.guardrail_passed:
        return "blocked"

    if decision.needs_registry:
        return "registry"

    next_nodes: list[str] = []
    if decision.needs_telemedicine:
        next_nodes.append("telemedicine")
    if decision.needs_scheduler:
        next_nodes.append("scheduler")
    if decision.needs_notification:
        next_nodes.append("notification")

    return next_nodes or "finalize"


def route_after_registry(state: GraphState):
    decision: RouterDecision | None = state.get("router_decision")
    registry = state.get("registry_result")
    if not registry:
        return "finalize"

    if registry.status in {"awaiting_cpf", "awaiting_patient_data", "integration_error"}:
        return "finalize"

    if decision and decision.needs_telemedicine:
        return "telemedicine"
    if decision and decision.needs_scheduler:
        return "scheduler"
    if decision and decision.needs_notification:
        return "notification"
    return "finalize"


def route_after_telemedicine(state: GraphState):
    decision: RouterDecision | None = state.get("router_decision")
    telemedicine = state.get("telemedicine_result")
    if not telemedicine:
        return "finalize"
    if decision and decision.requested_action == "agendar":
        return "finalize"
    if decision and decision.needs_notification and telemedicine.requires_appointment:
        return "notification"
    return "finalize"


def route_after_scheduler(state: GraphState):
    decision: RouterDecision | None = state.get("router_decision")
    if decision and decision.needs_notification:
        return "notification"
    return "finalize"


def _build_graph(checkpointer):
    builder = StateGraph(GraphState)

    builder.add_node("root", node_root)
    builder.add_node("blocked", node_blocked_response)
    builder.add_node("scheduler", node_scheduler)
    builder.add_node("registry", node_registry)
    builder.add_node("telemedicine", node_telemedicine)
    builder.add_node("notification", node_notification)
    builder.add_node("finalize", node_finalize_response)

    builder.add_edge(START, "root")
    builder.add_conditional_edges("root", route_after_root)

    builder.add_edge("blocked", END)
    builder.add_conditional_edges("registry", route_after_registry)
    builder.add_conditional_edges("telemedicine", route_after_telemedicine)
    builder.add_conditional_edges("scheduler", route_after_scheduler)
    builder.add_edge("notification", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


async def setup_checkpointer():
    global compiled_graph, _checkpointer_cm

    if compiled_graph is not None:
        return compiled_graph

    db_uri = os.getenv("DB_PATH", "").strip()
    if db_uri:
        logger.info("Usando AsyncPostgresSaver para persistencia.")
        _checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_uri)
        checkpointer = await _checkpointer_cm.__aenter__()
        await checkpointer.setup()
    else:
        logger.info("DB_PATH nao definida. Usando MemorySaver.")
        checkpointer = MemorySaver()

    compiled_graph = _build_graph(checkpointer)
    try:
        await setup_patient_registry()
    except PatientRegistryError as exc:
        logger.warning("Cadastro de pacientes indisponivel: %s", exc)
    return compiled_graph


async def shutdown_checkpointer():
    global compiled_graph, _checkpointer_cm

    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)
        _checkpointer_cm = None

    compiled_graph = None


def get_compiled_graph():
    if compiled_graph is None:
        raise RuntimeError("Graph not initialized. Call setup_checkpointer() first.")
    return compiled_graph
