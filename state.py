from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class RouterDecision(BaseModel):
    needs_scheduler: bool
    needs_registry: bool
    needs_telemedicine: bool
    needs_notification: bool
    intent: str
    requested_action: str
    urgency_level: str = "normal"
    patient_cpf: str | None = None
    patient_name: str | None = None
    patient_age: int | None = None
    patient_sex: str | None = None
    patient_email: str | None = None
    patient_phone: str | None = None
    doctor_name: str | None = None
    preferred_datetime: str | None = None
    payment_topic: str | None = None
    notify_channel: str = "whatsapp"
    summary: str | None = None
    guardrail_passed: bool = True
    rejection_reason: str | None = None


class ScheduleResult(BaseModel):
    action: str
    status: str
    summary: str
    suggested_slots: list[str] = Field(default_factory=list)


class RegistryResult(BaseModel):
    status: str
    patient_exists: bool
    patient_id: int | None = None
    patient_cpf: str | None = None
    patient_name: str | None = None
    patient_age: int | None = None
    patient_sex: str | None = None
    patient_email: str | None = None
    patient_phone: str | None = None
    fields_updated: list[str] = Field(default_factory=list)
    summary: str


class NotificationResult(BaseModel):
    status: str
    channel: str
    message_preview: str


class TelemedicineResult(BaseModel):
    status: str
    summary: str
    guidance: str
    recommended_next_step: str
    requires_appointment: bool = False
    references: list[str] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)


class GraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_query: str
    router_decision: RouterDecision | None
    schedule_result: ScheduleResult | None
    registry_result: RegistryResult | None
    telemedicine_result: TelemedicineResult | None
    notification_result: NotificationResult | None
    final_response: str | None
    retry_count: int
    error_log: list[str]
    fallback_triggered: bool
    human_approved: bool | None
    cache_hit: bool
