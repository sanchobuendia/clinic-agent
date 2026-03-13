import unittest

from graph import route_after_root
from state import RouterDecision


class RouteAfterRootTests(unittest.TestCase):
    def test_returns_blocked_when_guardrail_fails(self):
        state = {
            "router_decision": RouterDecision(
                needs_scheduler=False,
                needs_registry=False,
                needs_telemedicine=False,
                needs_notification=False,
                intent="blocked",
                requested_action="blocked",
                guardrail_passed=False,
                rejection_reason="blocked",
            )
        }
        self.assertEqual(route_after_root(state), "blocked")

    def test_routes_to_specialists_when_needed(self):
        state = {
            "router_decision": RouterDecision(
                needs_scheduler=True,
                needs_registry=True,
                needs_telemedicine=True,
                needs_notification=True,
                intent="agendar",
                requested_action="agendar",
            )
        }
        self.assertEqual(route_after_root(state), "registry")

    def test_routes_directly_to_synthesis_when_no_specialist_is_needed(self):
        state = {
            "router_decision": RouterDecision(
                needs_scheduler=False,
                needs_registry=False,
                needs_telemedicine=False,
                needs_notification=False,
                intent="geral",
                requested_action="geral",
            )
        }
        self.assertEqual(route_after_root(state), "finalize")
