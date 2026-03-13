import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


class MainApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    @patch("main.run_query", new_callable=AsyncMock)
    def test_query_endpoint_returns_graph_response(self, mock_run_query):
        mock_run_query.return_value = {
            "thread_id": "thread-1",
            "response": "Atendimento administrativo concluido.",
            "cache_hit": False,
            "fallback_triggered": False,
            "error_count": 0,
        }

        response = self.client.post("/query", json={"query": "Quero reagendar minha consulta."})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["response"], "Atendimento administrativo concluido.")
        mock_run_query.assert_awaited_once_with(
            "Quero reagendar minha consulta.",
            unittest.mock.ANY,
        )

    def test_query_endpoint_rejects_blank_queries(self):
        response = self.client.post("/query", json={"query": "   "})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "A query nao pode estar vazia.")

    @patch("main.send_email")
    @patch("main.GoogleCalendarService")
    def test_reminder_dispatch_endpoint_sends_windowed_reminders(self, mock_calendar_cls, mock_send_email):
        tz = timezone(timedelta(hours=-3))
        now = datetime.now(tz)
        event_2h = {
            "summary": "Consulta - Ana",
            "description": "CPF: 123 Email: ana@email.com",
            "start": {"dateTime": (now + timedelta(hours=2)).isoformat()},
            "end": {"dateTime": (now + timedelta(hours=2, minutes=30)).isoformat()},
        }
        service = mock_calendar_cls.return_value
        service.timezone = tz
        service.is_configured.return_value = True
        service.list_upcoming_events.return_value = [event_2h]

        response = self.client.post("/appointments/reminders")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["reminders_sent"], 1)
        mock_send_email.assert_called_once()

    def test_extract_email_from_event_is_case_insensitive(self):
        event = {"description": "email: PACIENTE@EXEMPLO.COM"}
        self.assertEqual(main._extract_email_from_event(event), "paciente@exemplo.com")

    @patch("main.send_email")
    @patch("main.GoogleCalendarService")
    def test_reminder_dispatch_endpoint_ignores_invalid_datetime(self, mock_calendar_cls, mock_send_email):
        tz = timezone(timedelta(hours=-3))
        service = mock_calendar_cls.return_value
        service.timezone = tz
        service.is_configured.return_value = True
        service.list_upcoming_events.return_value = [
            {
                "id": "evt-invalid",
                "summary": "Consulta - Ana",
                "description": "Email: ana@email.com",
                "start": {"dateTime": "invalid-date"},
            }
        ]

        response = self.client.post("/appointments/reminders")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inspected_events"], 1)
        mock_send_email.assert_not_called()
