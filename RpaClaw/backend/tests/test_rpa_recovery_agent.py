import unittest

from backend.rpa.recovery_agent import RecoveryAgent


class _FakeAgent:
    def __init__(self, events):
        self._events = events
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        for event in self._events:
            yield event


class RecoveryAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_recover_returns_first_completed_step(self):
        fake_agent = _FakeAgent(
            [
                {"event": "agent_thought", "data": {"text": "inspect"}},
                {"event": "agent_step_done", "data": {"step": {"action": "click", "description": "retry click"}}},
                {"event": "agent_done", "data": {"total_steps": 1}},
            ]
        )
        recovery_agent = RecoveryAgent(agent_factory=lambda: fake_agent)

        result = await recovery_agent.recover(
            session_id="session-1",
            page=object(),
            failing_step={"description": "Click submit"},
            error="button missing",
            existing_steps=[{"description": "Open page"}],
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["step"], {"action": "click", "description": "retry click"})
        self.assertIn("Click submit", fake_agent.calls[0]["goal"])
        self.assertIn("button missing", fake_agent.calls[0]["goal"])

    async def test_recover_returns_failure_when_agent_aborts(self):
        fake_agent = _FakeAgent(
            [
                {"event": "agent_aborted", "data": {"reason": "cannot recover"}},
            ]
        )
        recovery_agent = RecoveryAgent(agent_factory=lambda: fake_agent)

        result = await recovery_agent.recover(
            session_id="session-1",
            page=object(),
            failing_step={"description": "Click submit"},
            error="button missing",
            existing_steps=[],
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "cannot recover")


if __name__ == "__main__":
    unittest.main()
