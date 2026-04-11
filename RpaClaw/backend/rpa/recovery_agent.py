from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .assistant import RPAReActAgent, run_agent_until_stop


class RecoveryAgent:
    """Narrow wrapper for environment recovery using the existing ReAct agent."""

    def __init__(self, agent_factory: Optional[Callable[[], Any]] = None):
        self._agent_factory = agent_factory or RPAReActAgent

    async def recover(
        self,
        session_id: str,
        page: Any,
        failing_step: Dict[str, Any],
        error: str,
        existing_steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Any]]] = None,
    ) -> Dict[str, Any]:
        agent = self._agent_factory()
        goal = self._build_goal(failing_step=failing_step, error=error)
        return await run_agent_until_stop(
            agent,
            session_id=session_id,
            page=page,
            goal=goal,
            existing_steps=existing_steps,
            model_config=model_config,
            page_provider=page_provider,
        )

    @staticmethod
    def _build_goal(failing_step: Dict[str, Any], error: str) -> str:
        description = failing_step.get("description") or failing_step.get("action") or "script step"
        return (
            "Recover the browser environment so the failed script step can be retried.\n"
            f"Failed step: {description}\n"
            f"Error: {error}\n"
            "Do not continue the main task beyond the minimum recovery action."
        )
