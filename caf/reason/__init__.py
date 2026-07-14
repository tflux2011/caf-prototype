"""Real hosted-API reasoners for CAF (Phase D).

The deterministic playbook in :mod:`caf.agent.diagnosis` stays as the offline
baseline; this package provides a drop-in reasoner backed by a real model so
RQ1 can report genuine localization accuracy and token cost.
"""

from .openai_reasoner import (
    MissingAPIKey,
    OpenAIReasoner,
    ReasonerError,
)

__all__ = ["OpenAIReasoner", "ReasonerError", "MissingAPIKey"]
