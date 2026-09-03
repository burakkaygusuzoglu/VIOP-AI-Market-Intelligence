"""The Claude synthesis adapter (§29).

**No test here needs an API key, a network connection or a paid call.** The
adapter takes a `SynthesisTransport`, so every path - success, malformed JSON,
schema violation, timeout, rate limit, missing usage - is exercised with a fake
that returns whatever the test needs.

Every value is TEST_FIXTURE data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from app.adapters.synthesis.claude_synthesizer import (
    ClaudeMarketSynthesizer,
    SynthesizerConfig,
)
from app.adapters.synthesis.transport import (
    SynthesisTransportRequest,
    SynthesisTransportResponse,
    SynthesisUsage,
)
from app.application.ports.synthesis import SynthesisRequest
from app.application.synthesis.errors import SynthesisFailure, SynthesisProviderError
from app.application.synthesis.prompt import build_prompt
from app.domain.analysis.scenarios import ScenarioCase
from app.domain.synthesis.actions import FinalAction
from tests.factories_synthesis import minimal_context

MODEL = "fixture-synthesis-model"


@dataclass
class FakeTransport:
    """A `SynthesisTransport` returning whatever the test needs."""

    text: str = ""
    error: SynthesisProviderError | None = None
    usage: SynthesisUsage | None = None
    sent: list[SynthesisTransportRequest] = field(default_factory=list)

    async def send(self, request: SynthesisTransportRequest) -> SynthesisTransportResponse:
        self.sent.append(request)
        if self.error is not None:
            raise self.error
        return SynthesisTransportResponse(
            text=self.text,
            usage=self.usage if self.usage is not None else SynthesisUsage(model=MODEL),
        )


def response_payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "proposed_action": "WAIT",
        "summary": "Kurulum var, teyit bekleniyor.",
        "bull": {"case": "BULL", "narrative": "Yükseliş okuması."},
        "bear": {"case": "BEAR", "narrative": "Düşüş okuması."},
        "neutral": {"case": "NEUTRAL", "narrative": "Nötr okuma."},
        "devils_advocate": {"challenge": "Karşı görüş.", "evidence_is_limited": True},
    }
    body.update(overrides)
    return body


def synthesizer(text: str = "", error: SynthesisProviderError | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
    transport = FakeTransport(
        text=text or json.dumps(response_payload()),
        error=error,
        **kwargs,  # type: ignore[arg-type]
    )
    return ClaudeMarketSynthesizer(transport, SynthesizerConfig(model=MODEL)), transport


def request_for(context: object = None) -> SynthesisRequest:
    ctx = context if context is not None else minimal_context()
    return SynthesisRequest(context=ctx, prompt=build_prompt(ctx))  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# The happy path
# ----------------------------------------------------------------------


@pytest.mark.unit
async def test_a_valid_response_becomes_a_typed_draft() -> None:
    engine, transport = synthesizer()
    draft = await engine.synthesize(request_for())

    assert draft.proposed_action is FinalAction.WAIT
    assert {item.case for item in draft.narratives} == set(ScenarioCase)
    assert transport.sent[0].model == MODEL


@pytest.mark.unit
async def test_the_carried_prompt_is_what_gets_sent() -> None:
    """Not a second rendering - the text the budget measured."""
    engine, transport = synthesizer()
    request = request_for()
    await engine.synthesize(request)

    assert transport.sent[0].system == request.prompt.system
    assert transport.sent[0].user_message == request.prompt.user


@pytest.mark.unit
async def test_the_system_prompt_states_the_rules_it_must() -> None:
    engine, transport = synthesizer()
    await engine.synthesize(request_for())
    system = " ".join(transport.sent[0].system.split())

    for phrase in (
        "You interpret and explain. You are never the source of truth for a number.",
        "You may ONLY propose an action listed in ALLOWED ACTIONS",
        "The risk veto cannot be overridden",
        "Never invent a reference id",
        "Never state a probability",
        "Setup Quality is a heuristic score, not a probability",
        "Missing information stays missing",
        "A Devil's Advocate section is mandatory",
        "Never give broker, order-placement or execution instructions",
    ):
        assert phrase in system, f"the prompt no longer says: {phrase}"


@pytest.mark.unit
async def test_the_allowed_actions_reach_the_prompt() -> None:
    engine, transport = synthesizer()
    await engine.synthesize(request_for())
    assert "ALLOWED ACTIONS: LONG, WAIT, NO_TRADE" in transport.sent[0].user_message


# ----------------------------------------------------------------------
# Malformed output is refused, never repaired
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "text"),
    (
        ("empty", "   "),
        ("prose", "I think you should go long."),
        ("truncated json", '{"proposed_action": "WAIT"'),
        ("json array", "[1, 2, 3]"),
        ("json string", '"WAIT"'),
        ("null", "null"),
    ),
)
async def test_malformed_output_is_refused(label: str, text: str) -> None:
    engine, _ = synthesizer(text=text)
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())
    assert excinfo.value.failure is SynthesisFailure.INVALID_OUTPUT, label


@pytest.mark.unit
async def test_an_unexpected_field_is_refused_not_stripped() -> None:
    engine, _ = synthesizer(text=json.dumps(response_payload(risk_permission=True)))
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())
    assert excinfo.value.failure is SynthesisFailure.INVALID_OUTPUT


@pytest.mark.unit
@pytest.mark.parametrize(
    "forged",
    (
        {"allowed_actions": ["LONG"]},
        {"risk_permission": True},
        {"maximum_contracts": 5},
        {"blocker_override": True},
        {"data_quality_override": "ACCEPTED"},
        {"margin_override": 1},
        {"force_trade": True},
        {"ignore_risk": True},
    ),
)
async def test_a_model_cannot_address_the_envelope_at_all(forged: dict[str, object]) -> None:
    """§16: there is no field for these, so `extra="forbid"` refuses them."""
    engine, _ = synthesizer(text=json.dumps(response_payload(**forged)))
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())
    assert excinfo.value.failure is SynthesisFailure.INVALID_OUTPUT


@pytest.mark.unit
async def test_an_invalid_enum_is_refused() -> None:
    engine, _ = synthesizer(text=json.dumps(response_payload(proposed_action="BUY")))
    with pytest.raises(SynthesisProviderError):
        await engine.synthesize(request_for())


@pytest.mark.unit
@pytest.mark.parametrize("section", ("bull", "bear", "neutral", "devils_advocate"))
async def test_a_missing_mandatory_section_is_refused(section: str) -> None:
    payload = response_payload()
    del payload[section]
    engine, _ = synthesizer(text=json.dumps(payload))
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())
    assert excinfo.value.failure is SynthesisFailure.INVALID_OUTPUT


@pytest.mark.unit
async def test_scenario_cases_in_the_wrong_slots_are_refused() -> None:
    """Field names satisfied, content swapped - structurally valid, wrong."""
    engine, _ = synthesizer(
        text=json.dumps(response_payload(bull={"case": "BEAR", "narrative": "x"}))
    )
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())
    assert "not one of each case" in excinfo.value.detail


@pytest.mark.unit
async def test_a_rejection_never_echoes_model_content() -> None:
    """A model must not be able to write a log line by returning one."""
    marker = "MODEL-SUPPLIED-MARKER-9f3a"
    engine, _ = synthesizer(text=json.dumps(response_payload(summary=marker, oops=marker)))
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())
    assert marker not in excinfo.value.detail
    assert marker not in excinfo.value.public_detail


@pytest.mark.unit
async def test_invalid_output_is_not_retryable() -> None:
    """Re-asking a model that returned malformed output is not a strategy."""
    assert not SynthesisFailure.INVALID_OUTPUT.retryable
    assert not SynthesisFailure.CONTEXT_TOO_LARGE.retryable


# ----------------------------------------------------------------------
# Provider failures
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "retryable"),
    (
        (SynthesisFailure.TIMEOUT, True),
        (SynthesisFailure.NETWORK, True),
        (SynthesisFailure.RATE_LIMITED, True),
        (SynthesisFailure.UNAVAILABLE, True),
        (SynthesisFailure.AUTHENTICATION, False),
        (SynthesisFailure.PROVIDER_REJECTED, False),
        (SynthesisFailure.NOT_CONFIGURED, False),
    ),
)
async def test_each_provider_failure_propagates_with_its_retry_semantics(
    failure: SynthesisFailure, retryable: bool
) -> None:
    engine, _ = synthesizer(error=SynthesisProviderError(failure, "internal detail"))
    with pytest.raises(SynthesisProviderError) as excinfo:
        await engine.synthesize(request_for())

    assert excinfo.value.failure is failure
    assert excinfo.value.retryable is retryable


@pytest.mark.unit
def test_a_public_error_message_never_leaks_provider_detail() -> None:
    error = SynthesisProviderError(
        SynthesisFailure.AUTHENTICATION,
        "x-api-key sk-ant-SECRET rejected by api.anthropic.com",
    )
    assert "sk-ant" not in error.public_detail
    assert "anthropic.com" not in error.public_detail
    assert error.public_detail


@pytest.mark.unit
def test_every_failure_has_a_public_phrase() -> None:
    for failure in SynthesisFailure:
        assert SynthesisProviderError(failure, "x").public_detail.strip()


@pytest.mark.unit
def test_no_failure_status_is_a_market_action() -> None:
    """§22 at the type level: the vocabularies are disjoint."""
    actions = {item.value for item in FinalAction}
    for failure in SynthesisFailure:
        assert failure.status.value not in actions


# ----------------------------------------------------------------------
# Configuration and usage
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unconfigured_model_refuses_to_build() -> None:
    """§11: no implicit fallback to a model nobody verified."""
    with pytest.raises(SynthesisProviderError) as excinfo:
        SynthesizerConfig(model="   ")
    assert excinfo.value.failure is SynthesisFailure.NOT_CONFIGURED


@pytest.mark.unit
def test_a_transport_without_a_key_refuses_to_build() -> None:
    from app.adapters.synthesis.transport import AnthropicSynthesisTransport  # noqa: PLC0415

    with pytest.raises(SynthesisProviderError) as excinfo:
        AnthropicSynthesisTransport(api_key="  ")
    assert excinfo.value.failure is SynthesisFailure.NOT_CONFIGURED


@pytest.mark.unit
async def test_usage_is_captured_when_the_provider_reports_it() -> None:
    engine, _ = synthesizer(
        usage=SynthesisUsage(input_tokens=1200, output_tokens=340, model=MODEL, request_id="req_1")
    )
    await engine.synthesize(request_for())

    assert engine.last_usage.input_tokens == 1200
    assert engine.last_usage.output_tokens == 340
    assert engine.last_usage.request_id == "req_1"


@pytest.mark.unit
async def test_absent_usage_stays_none_and_is_never_zero() -> None:
    """§26: a fabricated zero is indistinguishable from a genuinely free call."""
    engine, _ = synthesizer(usage=SynthesisUsage(model=MODEL))
    await engine.synthesize(request_for())

    assert engine.last_usage.input_tokens is None
    assert engine.last_usage.output_tokens is None


@pytest.mark.unit
async def test_the_output_token_ceiling_is_the_smaller_of_the_two() -> None:
    engine, transport = synthesizer()
    context = minimal_context()
    await engine.synthesize(
        SynthesisRequest(context=context, prompt=build_prompt(context), max_output_tokens=500)
    )
    assert transport.sent[0].max_tokens == 500
