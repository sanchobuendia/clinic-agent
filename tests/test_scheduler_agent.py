import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from agents.scheduler_agent import scheduler_agent
from services.google_calendar import CalendarSlot
from state import RouterDecision


class SchedulerAgentTests(unittest.IsolatedAsyncioTestCase):
    @patch("agents.scheduler_agent.GoogleCalendarService")
    async def test_cancel_action_cancels_existing_event(self, mock_calendar_cls):
        mock_service = mock_calendar_cls.return_value
        mock_service.timezone = datetime.now().astimezone().tzinfo
        mock_service.is_configured.return_value = True
        mock_service.find_patient_event.return_value = {
            "id": "evt-1",
            "start": {"dateTime": "2026-03-25T15:00:00-03:00"},
        }

        state = {
            "user_query": "quero cancelar",
            "router_decision": RouterDecision(
                needs_scheduler=True,
                needs_registry=True,
                needs_telemedicine=False,
                needs_notification=True,
                intent="cancelar",
                requested_action="cancelar",
                patient_cpf="12345678909",
                patient_name="Ana Silva",
            ),
            "registry_result": type("R", (), {"status": "found"})(),
            "error_log": [],
        }

        result = await scheduler_agent(state)
        self.assertEqual(result["schedule_result"].status, "cancelled")
        mock_service.cancel_event.assert_called_once_with(event_id="evt-1")

    @patch("agents.scheduler_agent.GoogleCalendarService")
    async def test_remarcar_action_updates_event_when_slot_available(self, mock_calendar_cls):
        mock_service = mock_calendar_cls.return_value
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        mock_service.timezone = now.astimezone().tzinfo
        mock_service.is_configured.return_value = True
        mock_service.find_patient_event.return_value = {"id": "evt-2"}
        slot = CalendarSlot(start=now + timedelta(days=2), end=now + timedelta(days=2, minutes=30))
        mock_service.find_available_slots.return_value = (slot.start, [slot])
        mock_service.reschedule_event.return_value = {"htmlLink": "https://calendar.google.com/event"}

        state = {
            "user_query": "quero remarcar para 25/03/2026 as 15h",
            "router_decision": RouterDecision(
                needs_scheduler=True,
                needs_registry=True,
                needs_telemedicine=False,
                needs_notification=True,
                intent="remarcar",
                requested_action="remarcar",
                patient_cpf="12345678909",
                patient_name="Ana Silva",
                preferred_datetime="25/03/2026 as 15h",
            ),
            "registry_result": type("R", (), {"status": "found"})(),
            "error_log": [],
        }

        result = await scheduler_agent(state)
        self.assertEqual(result["schedule_result"].status, "slot_reserved")
        mock_service.reschedule_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
