"""Source-status response schemas (Phase 15 Part 2B). Read-only.

There are no request bodies: the source API has only GET routes, and nothing
in it can submit, approve, verify or publish a fact.

Some truths are fixed in the type system so no response can be built claiming
otherwise in this build: ``real_provider_connected`` and
``financial_use_enabled`` are ``Literal[False]``, and
``verification_writes`` names the one place a review can be written - the
local operator command, never HTTP.

Times: ``applies_at``, ``effective_*`` and ``at`` are market time;
``known_by``, ``known_at`` and ``recorded_at`` are this system's knowledge and
audit clock; ``verified_at`` and ``decided_at`` are the reviewer's stated
review time; ``submitted_at`` is the operator's stated submission time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

_PUBLIC = ConfigDict(extra="forbid", frozen=True)

SYMBOL_PATTERN = r"^[A-Za-z0-9._-]{1,32}$"

Category = Literal[
    "MARKET_DATA",
    "CONTRACT_METADATA",
    "OPEN_INTEREST",
    "NEWS",
    "MARKET_BREADTH",
    "SESSION_CALENDAR",
]
Capability = Literal["NOT_CONFIGURED", "NOT_LICENSED", "UNAVAILABLE", "AVAILABLE", "STALE"]


class DeploymentResponse(BaseModel):
    model_config = _PUBLIC

    market_data_provider: str
    real_provider_connected: Literal[False]
    simulated_market_data: StrictBool
    calendar_source_composed: StrictBool
    verification_writes: Literal["LOCAL_OPERATOR_COMMAND_ONLY"]
    reviewer_identity: Literal["OPERATOR_ASSERTION_NOT_AUTHENTICATED"]
    financial_use_enabled: Literal[False]


class CategoryResponse(BaseModel):
    model_config = _PUBLIC

    category: Category
    status: Capability
    reason: str
    configured: StrictBool
    licensed: StrictBool
    connected: StrictBool
    available: StrictBool
    fresh: StrictBool
    verified: StrictBool
    adapter_in_build: StrictBool


class JournalCountsResponse(BaseModel):
    model_config = _PUBLIC

    submissions: StrictInt
    approved: StrictInt
    rejected: StrictInt
    refused: StrictInt
    records: StrictInt


class SourceCapabilitiesResponse(BaseModel):
    model_config = _PUBLIC

    deployment: DeploymentResponse
    categories: list[CategoryResponse]
    journal: JournalCountsResponse
    server_time: str


class FieldResponse(BaseModel):
    model_config = _PUBLIC

    name: Literal[
        "multiplier",
        "tick_size",
        "tick_value",
        "expiry_date",
        "initial_margin",
        "maintenance_margin",
    ]
    state: Literal["VERIFIED", "MISSING", "NOT_REVIEWABLE", "UNAVAILABLE"]
    value: str | None
    source: str | None
    verified_at: str | None


class ChecksResponse(BaseModel):
    model_config = _PUBLIC

    source_claims_value: StrictBool
    operator_examined_evidence: StrictBool
    source_authority_assessed: StrictBool
    applicable_to_contract: StrictBool
    applicable_at_market_time: StrictBool
    known_by_requested_time: StrictBool
    current: StrictBool
    financial_use_enabled: Literal[False]


class RecordResponse(BaseModel):
    model_config = _PUBLIC

    record_id: str
    authority: Literal["EXCHANGE_OFFICIAL", "LICENSED_PROVIDER"]
    reference: str
    effective_from: str
    effective_until: str | None
    verified_at: str
    known_at: str | None
    corrects: str | None
    reviewed_by: str | None
    multiplier: str
    tick_size: str
    expiry_date: str | None


class ConflictResponse(BaseModel):
    model_config = _PUBLIC

    fact: str
    chosen_record: str
    chosen_value: str
    other_record: str
    other_value: str
    resolution: str


class MetadataStatusResponse(BaseModel):
    model_config = _PUBLIC

    symbol: str
    applies_at: str
    known_by: str | None
    retrospective: StrictBool
    verdict: str
    reason: str
    governing_record: str | None
    fields: list[FieldResponse]
    checks: ChecksResponse
    conflicts: list[ConflictResponse]
    superseded: list[str]
    records: list[RecordResponse]
    financial_use_enabled: Literal[False]
    server_time: str


class CalendarStatusResponse(BaseModel):
    model_config = _PUBLIC

    symbol: str
    at: str
    status: Literal["IN_SESSION", "OUT_OF_SESSION", "UNAVAILABLE"]
    reason: str
    source: str | None
    source_verified_at: str | None
    calendar_source_composed: StrictBool
    server_time: str


class DecisionResponse(BaseModel):
    model_config = _PUBLIC

    reviewer: str
    reviewer_identity: Literal["OPERATOR_ASSERTION_NOT_AUTHENTICATED"]
    decided_at: str
    outcome: Literal["APPROVED", "REJECTED"]
    document_checked: StrictBool
    note: str
    result: Literal["APPROVED", "REJECTED", "REFUSED"]
    refusal_code: str | None
    recorded_at: str


class ReviewEntryResponse(BaseModel):
    model_config = _PUBLIC

    sequence: StrictInt
    submission_id: str
    symbol: str
    fact: Literal["MULTIPLIER", "TICK_SIZE", "EXPIRY_DATE"]
    claimed_value: str | None
    reference: str
    authority: Literal["EXCHANGE_OFFICIAL", "LICENSED_PROVIDER", "SECONDARY", "UNKNOWN"]
    effective_from: str | None
    effective_until: str | None
    submitted_by: str
    submitted_at: str
    origin: Literal["MANUAL_ENTRY", "FILE_IMPORT"]
    corrects: str | None
    recorded_at: str
    decision: DecisionResponse | None


class ReviewPageResponse(BaseModel):
    model_config = _PUBLIC

    items: list[ReviewEntryResponse]
    total: StrictInt
    next_after: StrictInt | None
    server_time: str
