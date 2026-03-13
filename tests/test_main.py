import unittest
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
