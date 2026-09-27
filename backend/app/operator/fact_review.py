"""The contract-fact review command: ``python -m app.operator.fact_review``.

The only writer of the verification journal. It is a composition root, like
``app/main.py``: it wires the PostgreSQL store to the application service, and
nothing else imports it.

## What it is, and what it is not

It is a local, explicit, auditable way for the person operating this
deployment to record what an official document says, and to record their own
review of that claim. It is **not** authentication: ``--submitted-by`` and
``--reviewer`` are the operator's assertions, journalled as such and shown as
``OPERATOR_ASSERTION_NOT_AUTHENTICATED`` by the API. Its protection is that it
needs shell and database access on the host - which is exactly the access that
could rewrite the database anyway.

## What it never does

It never fetches a URL, opens a file named in a reference, or reads a
spreadsheet: a reference is text a reviewer checks by hand. It never backdates:
submission and decision times are this machine's clock at the moment the
command runs. It never approves without ``--document-checked``. And an
approved, published record is still not used by any financial consumer.

## Commands

::

    submit   --id ID --symbol S --fact MULTIPLIER|TICK_SIZE|EXPIRY_DATE --value V
             --reference REF --authority A --effective-from T [--effective-until T]
             --submitted-by NAME [--origin MANUAL_ENTRY|FILE_IMPORT] [--corrects RECORD]
    decide   --id ID --reviewer NAME --outcome APPROVED|REJECTED [--document-checked]
             [--note TEXT]
    publish  --record-id R --underlying U --name N --multiplier ID --tick-size ID
             [--expiry ID]
    history  [--after N] [--limit N]

Exit status: 0 done, 1 refused (the code is printed), 2 malformed arguments,
3 the journal is unreachable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TextIO

from app.adapters.persistence.database import Database
from app.adapters.persistence.fact_store import SqlAlchemyFactVerificationStore
from app.adapters.system.clock import SystemClock
from app.application.ports.fact_verification import (
    VerificationConflictError,
    VerificationStoreUnavailableError,
)
from app.application.ports.system import ClockPort
from app.application.sourcing.fact_review import FactVerificationService
from app.core.config import get_settings
from app.core.runtime import configure_event_loop_policy
from app.domain.sourcing.facts import SourceAuthority
from app.domain.sourcing.review import (
    ContractFact,
    FactSubmission,
    ReviewDecision,
    ReviewOutcome,
    ReviewRefusedError,
    SubmissionOrigin,
)

__all__ = ["main", "run"]

OK, REFUSED, MALFORMED, UNREACHABLE = 0, 1, 2, 3
MAX_HISTORY = 100


class _MalformedError(ValueError):
    pass


def _moment(text: str | None) -> datetime | None:
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise _MalformedError("a time is ISO-8601, e.g. 2026-03-02T00:00:00+03:00") from None
    if moment.utcoffset() is None:
        raise _MalformedError("a time states its UTC offset")
    return moment


def _value(fact: ContractFact, text: str) -> Decimal | date:
    if fact is ContractFact.EXPIRY_DATE:
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise _MalformedError("an expiry date is YYYY-MM-DD") from None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise _MalformedError("a value is an exact decimal, e.g. 0.25") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.operator.fact_review",
        description="Record and review contract facts. Local operator use only.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    submit = commands.add_parser("submit", help="journal a claim that a document states a fact")
    submit.add_argument("--id", required=True)
    submit.add_argument("--symbol", required=True)
    submit.add_argument("--fact", required=True, choices=[f.value for f in ContractFact])
    submit.add_argument("--value", required=True)
    submit.add_argument("--reference", required=True)
    submit.add_argument("--authority", required=True, choices=[a.value for a in SourceAuthority])
    submit.add_argument("--effective-from")
    submit.add_argument("--effective-until")
    submit.add_argument("--submitted-by", required=True)
    submit.add_argument(
        "--origin",
        choices=[o.value for o in SubmissionOrigin],
        default=SubmissionOrigin.MANUAL_ENTRY.value,
    )
    submit.add_argument("--corrects")

    decide = commands.add_parser("decide", help="journal a review decision")
    decide.add_argument("--id", required=True)
    decide.add_argument("--reviewer", required=True)
    decide.add_argument("--outcome", required=True, choices=[o.value for o in ReviewOutcome])
    decide.add_argument(
        "--document-checked",
        action="store_true",
        help="I opened the referenced document and confirmed value, contract, period and publisher",
    )
    decide.add_argument("--note", default="")

    publish = commands.add_parser("publish", help="publish a record from approved facts")
    publish.add_argument("--record-id", required=True)
    publish.add_argument("--underlying", required=True)
    publish.add_argument("--name", required=True)
    publish.add_argument("--multiplier", required=True)
    publish.add_argument("--tick-size", required=True)
    publish.add_argument("--expiry")

    history = commands.add_parser("history", help="print journalled submissions and decisions")
    history.add_argument("--after", type=int, default=0)
    history.add_argument("--limit", type=int, default=50)
    return parser


def _emit(out: TextIO, payload: dict[str, object]) -> None:
    out.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


async def run(
    argv: Sequence[str],
    *,
    database: Database,
    clock: ClockPort,
    out: TextIO = sys.stdout,
) -> int:
    """Run one command against ``database``. Returns the exit status."""
    try:
        args = _parser().parse_args(list(argv))
    except SystemExit as exit_:
        return MALFORMED if exit_.code else OK
    store = SqlAlchemyFactVerificationStore(database)
    service = FactVerificationService(store, clock)
    try:
        if args.command == "submit":
            fact = ContractFact(args.fact)
            written = await service.submit(
                FactSubmission(
                    submission_id=args.id,
                    symbol=args.symbol,
                    fact=fact,
                    value=_value(fact, args.value),
                    reference=args.reference,
                    authority=SourceAuthority(args.authority),
                    effective_from=_moment(args.effective_from),
                    effective_until=_moment(args.effective_until),
                    submitted_by=args.submitted_by,
                    submitted_at=clock.now(),
                    origin=SubmissionOrigin(args.origin),
                    corrects=args.corrects,
                )
            )
            _emit(out, {"submission": args.id, "written": written, "verified": False})
        elif args.command == "decide":
            journalled = await service.decide(
                ReviewDecision(
                    submission_id=args.id,
                    reviewer=args.reviewer,
                    decided_at=clock.now(),
                    outcome=ReviewOutcome(args.outcome),
                    document_checked=bool(args.document_checked),
                    note=args.note,
                )
            )
            _emit(
                out,
                {
                    "submission": args.id,
                    "result": journalled.result.value,
                    "refusal_code": journalled.refusal_code,
                    "reviewer_identity": "OPERATOR_ASSERTION_NOT_AUTHENTICATED",
                },
            )
            if journalled.refusal_code is not None:
                return REFUSED
        elif args.command == "publish":
            record = await service.publish(
                args.record_id,
                underlying_symbol=args.underlying,
                contract_name=args.name,
                multiplier=args.multiplier,
                tick_size=args.tick_size,
                expiry=args.expiry,
            )
            _emit(
                out,
                {
                    "record": record.record_id,
                    "symbol": record.contract.symbol,
                    "financial_use_enabled": False,
                },
            )
        else:
            if not (args.after >= 0 and 1 <= args.limit <= MAX_HISTORY):
                raise _MalformedError(f"after is non-negative and limit is 1-{MAX_HISTORY}")
            entries, total = await store.review_page(after=args.after, limit=args.limit)
            for entry in entries:
                decision = entry.decision
                _emit(
                    out,
                    {
                        "sequence": entry.sequence,
                        "submission": entry.submission.submission_id,
                        "symbol": entry.submission.symbol,
                        "fact": entry.submission.fact.value,
                        "result": None if decision is None else decision.result.value,
                        "refusal_code": None if decision is None else decision.refusal_code,
                    },
                )
            _emit(out, {"total": total})
    except _MalformedError as error:
        _emit(out, {"error": "MALFORMED", "detail": str(error)})
        return MALFORMED
    except (ReviewRefusedError, VerificationConflictError) as error:
        _emit(out, {"error": error.code})
        return REFUSED
    except VerificationStoreUnavailableError:
        _emit(out, {"error": "JOURNAL_UNREACHABLE"})
        return UNREACHABLE
    return OK


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], ClockPort] = SystemClock) -> int:
    configure_event_loop_policy()
    settings = get_settings()

    async def _go() -> int:
        database = Database(
            settings.sqlalchemy_url, connect_timeout=settings.db_connect_timeout_seconds
        )
        try:
            return await run(
                sys.argv[1:] if argv is None else argv, database=database, clock=clock()
            )
        finally:
            await database.dispose()

    return asyncio.run(_go())


if __name__ == "__main__":
    raise SystemExit(main())
