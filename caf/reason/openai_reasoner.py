"""Real hosted-API reasoner for CAF (Phase D).

This swaps a real OpenAI model in behind the *same* decision the deterministic
playbook implements, so RQ1 can be measured with a genuine model instead of a
hand-written policy. The two arms remain identical in everything except what
they are shown:

* the **grounded** arm receives the RST slice around the observing service --
  its dependency edges and each candidate subject's closed action set;
* the **ungrounded** arm receives only the local fault signal and a generic
  symptom playbook, with no topology at all.

Any accuracy gap between the arms is therefore attributable to the self-model,
exactly as in the deterministic harness.

Safety is not delegated to the model. Whatever the model returns, the admission
boundary (Theorem 1) is re-imposed *in code*: a proposed action is kept only if
it belongs to the suspected subject's closed action set, and a subject with no
permitted actions (an external dependency) always escalates. The model can
mislocalize, but it cannot cause an inadmissible repair.

Security: the API key is read from the ``OPENAI_API_KEY`` environment variable
only -- never hard-coded, logged, or written to disk. Requests carry no secrets
beyond the bearer token the SDK-less httpx client sets on the wire over TLS.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, Optional

from caf.agent.signals import Diagnosis, FaultSignal
from caf.schema import RST

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


class ReasonerError(RuntimeError):
    """Raised when the reasoner cannot produce a valid diagnosis."""


class MissingAPIKey(ReasonerError):
    """Raised when no OpenAI API key is available in the environment."""


@dataclass
class ChatResult:
    """A single model completion plus its token accounting."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


# A transport turns a list of chat messages into a completion. Injectable so
# tests can run without any network access.
Transport = Callable[[list[dict[str, str]]], ChatResult]


SYSTEM_PROMPT = (
    "You are a site-reliability diagnosis function for a microservice mesh. "
    "You are given one fault as observed at a single service and must decide "
    "which node is the true root-cause subject and what to do about it.\n"
    "Rules:\n"
    "1. Only propose an action drawn from the SUBJECT's permitted action set. "
    "If you are not given that set, or the subject is an external dependency "
    "with no permitted actions, you must escalate and propose no action.\n"
    "2. Attribute by symptom origin, not by reflex. dependency_timeout and "
    "dependency_5xx propagate from a downstream node, so follow the matching "
    "edge to that node; but pool_timeout and latency_spike are local-resource "
    "symptoms that usually originate at the observing node itself, so do not "
    "blame a visible dependency for them without direct propagation evidence.\n"
    "3. Respond with a SINGLE JSON object and nothing else, with exactly these "
    "keys: suspected_subject (string), hypothesis (string), confidence (number "
    "0..1), proposed_action (string or null), escalate (boolean)."
)


def _grounded_user(signal: FaultSignal, rst: RST) -> str:
    """User prompt WITH the RST slice around the observing service."""
    lines = [
        f"Observed at service: {signal.service}",
        f"Symptom: {signal.symptom.value}",
        f"Evidence: {json.dumps(signal.evidence, sort_keys=True)}",
        "",
        "Topology (from the service's self-model, RST):",
    ]
    node = rst.nodes.get(signal.service)
    if node is not None:
        lines.append(
            f"- observing node {signal.service}: kind={node.kind}, "
            f"criticality={node.criticality}, "
            f"permitted_actions={sorted(node.permitted_actions)}"
        )
    outbound = [e for e in rst.edges if e.from_ == signal.service]
    if outbound:
        lines.append("- dependencies (edges out of the observing node):")
        for edge in outbound:
            dep = rst.nodes.get(edge.to)
            permitted = sorted(dep.permitted_actions) if dep else []
            kind = dep.kind if dep else "unknown"
            lines.append(
                f"    -> {edge.to} via {edge.kind}: kind={kind}, "
                f"permitted_actions={permitted}"
            )
    else:
        lines.append("- (no outbound dependencies)")
    lines.append(
        "\nSymptom semantics (how this class of symptom typically originates):"
    )
    lines.append(
        "- pool_timeout / latency_spike: local-resource symptoms at the "
        "observing node; the subject is usually the observing node itself "
        "unless a specific dependency's saturation clearly dominates."
    )
    lines.append(
        "- dependency_timeout / dependency_5xx: propagated from a downstream "
        "dependency; name the dependency reached over the matching edge kind "
        "(e.g. a sql edge for a timeout to a database, an http edge for 5xx to "
        "a service) as the subject."
    )
    lines.append(
        "\nAn empty permitted_actions set means the node cannot be repaired "
        "locally and the correct disposition is to escalate."
    )
    return "\n".join(lines)


# The only guidance the ungrounded arm has: a generic, topology-free playbook.
_UNGROUNDED_PLAYBOOK = {
    "pool_timeout": "restart the local connection pool",
    "dependency_timeout": "recycle local state after a timeout",
    "dependency_5xx": "trip the local circuit breaker",
    "latency_spike": "shed load / throttle",
    "retry_storm": "trip the local circuit breaker",
    "deployment_regression": "roll back the recent canary",
}


def _ungrounded_user(signal: FaultSignal) -> str:
    """User prompt WITHOUT any topology: only local telemetry and a playbook."""
    hint = _UNGROUNDED_PLAYBOOK.get(signal.symptom.value, "act locally")
    return (
        f"Observed at service: {signal.service}\n"
        f"Symptom: {signal.symptom.value}\n"
        f"Evidence: {json.dumps(signal.evidence, sort_keys=True)}\n\n"
        "You have only local telemetry; no dependency graph is available. "
        f"A generic playbook for this symptom is: {hint}. "
        "Decide the subject and action from what you can see."
    )


def _build_messages(signal: FaultSignal, rst: RST, grounded: bool) -> list[dict[str, str]]:
    user = _grounded_user(signal, rst) if grounded else _ungrounded_user(signal)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _strip_fences(text: str) -> str:
    """Tolerate a model that wraps JSON in a Markdown code fence."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[: -3]
        # drop a leading language tag like ``json``
        if stripped.lstrip().startswith("json"):
            stripped = stripped.lstrip()[4:]
    return stripped.strip()


def _parse(content: str) -> Diagnosis:
    try:
        payload = json.loads(_strip_fences(content))
    except json.JSONDecodeError as exc:
        raise ReasonerError(f"model did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReasonerError("model JSON was not an object")
    # Only the contract keys; ignore anything extra the model may add.
    try:
        return Diagnosis(
            suspected_subject=str(payload["suspected_subject"]),
            hypothesis=str(payload.get("hypothesis", "")),
            confidence=float(payload.get("confidence", 0.5)),
            proposed_action=(
                None if payload.get("proposed_action") in (None, "", "null")
                else str(payload["proposed_action"])
            ),
            escalate=bool(payload.get("escalate", False)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ReasonerError(f"model JSON missing required fields: {exc}") from exc


def _enforce_admission(diag: Diagnosis, rst: RST, grounded: bool) -> Diagnosis:
    """Re-impose the Theorem 1 admission boundary regardless of model output.

    The model may localize, but it may not propose an action outside the
    subject's closed set, and a subject with no permitted actions must escalate.
    """
    subject = rst.nodes.get(diag.suspected_subject)
    unrepairable = (
        subject is None
        or subject.kind == "external_dependency"
        or not subject.permitted_actions
    )
    action = diag.proposed_action
    escalate = diag.escalate
    if unrepairable:
        action, escalate = None, True
    elif action is not None and action not in set(subject.permitted_actions):
        action, escalate = None, True
    return diag.model_copy(
        update={"proposed_action": action, "escalate": escalate, "grounded": grounded}
    )


class _HttpxTransport:
    """Default transport: a minimal, SDK-less OpenAI chat call over httpx/TLS."""

    def __init__(self, model: str, temperature: float, api_key: str, timeout: float) -> None:
        self._model = model
        self._temperature = temperature
        self._api_key = api_key
        self._timeout = timeout

    def __call__(self, messages: list[dict[str, str]]) -> ChatResult:
        import httpx  # lazy: only needed when actually calling the API

        body = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = httpx.post(
                OPENAI_URL, json=body, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ReasonerError(f"OpenAI request failed: {type(exc).__name__}") from exc
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ChatResult(
            content=content,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
        )


class OpenAIReasoner:
    """A diagnosis reasoner backed by a real OpenAI model.

    Interchangeable with :func:`caf.agent.diagnosis.diagnose_grounded` /
    ``diagnose_ungrounded`` via :meth:`diagnose`. A ``transport`` may be injected
    for offline testing; otherwise a default httpx client is built from the
    ``OPENAI_API_KEY`` environment variable.
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        transport: Optional[Transport] = None,
        timeout: float = 30.0,
    ) -> None:
        self.model = model or os.environ.get("CAF_OPENAI_MODEL", DEFAULT_MODEL)
        self.temperature = temperature
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        if transport is not None:
            self._transport: Transport = transport
        else:
            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise MissingAPIKey(
                    "OPENAI_API_KEY is not set; export it to use the OpenAI reasoner."
                )
            self._transport = _HttpxTransport(self.model, temperature, key, timeout)

    def diagnose(self, signal: FaultSignal, rst: RST, *, grounded: bool) -> Diagnosis:
        messages = _build_messages(signal, rst, grounded)
        result = self._transport(messages)
        self.total_prompt_tokens += result.prompt_tokens
        self.total_completion_tokens += result.completion_tokens
        diag = _parse(result.content)
        return _enforce_admission(diag, rst, grounded)
