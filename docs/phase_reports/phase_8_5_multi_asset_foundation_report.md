# PHASE 8.5 COMPLETION REPORT — MULTI-ASSET ARCHITECTURE FOUNDATION

**Status: IMPLEMENTED.** An approved architectural extension to support the
project's long-term multi-asset direction (master spec line 30: "designed so that
the architecture can later support additional exchanges and asset classes"). The
master specification is unchanged and remains the project authority. Phase 0–8
reports are unchanged. **Nothing committed. Nothing pushed. Phase 9 not started.**

## 1. Baseline

`main` at `1c3d555` ("Complete Phase 8 professional analysis UI"), tracking
`origin/main`, clean working tree, `git diff --check` clean. Services healthy.
Backend **2 353 passed / 0 skipped** (PostgreSQL configured), frontend **266
passed**, **16** import contracts kept. No paper trading, live provider, broker
or execution code present.

## 2. VİOP coupling audit

| Location | Occurrence | Classification |
| --- | --- | --- |
| `domain/risk/sizing.py` | `size_position(..., contract: FuturesContract)`: multiplier, tick grid, initial margin, linear-valuation and metadata guards | **Futures-specific facts** inside **generic reasoning** — the central coupling |
| `domain/risk/margin.py` | `assess_margin(contract, ...)`: multiplier for notional, margin per contract | Same mix |
| `domain/risk/pnl.py` | `gross_pnl`/`calculate_pnl` take a point value; `calculate_contract_pnl` reads the contract | Formula generic; binding futures-specific |
| `domain/risk/whatif.py` | `simulate` generic; `simulate_contract` futures binding | Same |
| `domain/risk/reward.py` | price geometry only | Generic |
| `domain/futures/*` | contract, validation, basis, open interest | Futures-specific (correctly placed) |
| `domain/analysis` | `build_contract_evidence(basis, open_interest)` | Optional futures-derived evidence input; `None` when absent. Kept |
| `domain/technical` | `bollinger_multiplier` | Generic statistics — false positive |
| `domain/structure`, `domain/market` | "tick" in prose only; structure explicitly refuses a tick assumption | Generic |
| `domain/suitability`, `domain/synthesis` | read `PositionSizing` results | Generic consumers of a result |
| `application/analysis/orchestrator.py` | `_contract_for`, `_is_verified`, `size_position` call | Composition point |
| `api/schemas/analysis_projection.py` | `_contract` block | Futures record projection |
| Public names `allowed_contracts`, `loss_per_contract`, `max_contracts` | API and frontend | Futures vocabulary in a generic result; kept for compatibility (§16) |
| `adapters/market_data/synthetic_provider.py` | `Decimal("0.01")` quantisation | Mock-data adapter, labelled mock; outside the core |
| Currency literals (`TRY`, `USD`, …) in `domain`/`application` | none | — |

## 3. Architectural decisions

1. **Core ← policy ← product.** `app.domain.instrument` (identity, asset classes,
   `ProductPolicy`) depends only on `common`; `app.domain.risk` depends on
   `instrument` and never on `futures`; `app.domain.futures` plugs in.
2. **One cohesive `ProductPolicy`,** not a family of micro-interfaces.
3. **Composition over inheritance:** a `Protocol`, and `FuturesProductPolicy`
   wraps a `FuturesContract`.
4. **Move, don't rewrite.** Generic reasoning stayed in place; futures bindings
   moved with unchanged signatures; the tick-grid check moved verbatim.
5. **Parity recorded before the move** and required exactly afterwards.
6. **Dispatch once.** The orchestrator's `_product_policy` is the only place a
   server record becomes a policy *for calculation*. The policy is also built,
   from the same kind of record, by the API projection and the four futures
   entry points; §31 lists all three sites.

## 4. Generic instrument model

`InstrumentId(symbol, asset_class: VerifiedValue[AssetClass], quote_currency:
VerifiedValue[str] | None = None)`. Venue, ISIN, lot size and sessions were
evaluated and left out: no current or Phase 9 consumer reads them.

## 5. Asset-class model

`AssetClass`: `FUTURES`, `EQUITY`, `CRYPTO_SPOT`, `CRYPTO_PERPETUAL`, `FX`.
`IMPLEMENTATION` (read-only `MappingProxyType`) marks only `FUTURES` as
`IMPLEMENTED`. `require_implemented` raises `UnsupportedAssetClassError`.

## 6. Product-policy design

`ProductPolicy`: `instrument`, `capabilities`, `vocabulary`,
`require_calculable(op)`, `point_value() -> VerifiedValue[Decimal]`,
`price_increment_check(entry, stop)`, `margin_requirement()`. The generic
guard `require_product_calculable` runs, in order: implemented class →
whole-unit quantity established → product calculability.

## 7. FuturesProductPolicy

Point value = the contract's own `multiplier` `VerifiedValue` (identity-equal);
price increment = the Phase 3 tick-grid check moved unchanged; margin =
`MISSING` / `UNVERIFIED` / `KNOWN(value)` from `initial_margin`;
calculability = linear valuation then metadata consistency, the Phase 3 order.
The four futures entry points live in `app.domain.futures.risk`.

## 8. Provenance semantics

`InstrumentId.user_declared` is always `UNVERIFIED`, source `"user input"`, no
currency. No path turns a typed symbol or class into a verified fact.
`VerificationStatus` is unchanged.

**Corrected in the human-review closeout (§31).** The first version derived the
futures classification's status from the multiplier and tick size. That coupled
two different facts and was removed: the classification now follows only
`FuturesContract.classification`, its own source, and is `UNVERIFIED` when no
such source exists - which is every record today.

## 9. Quantity semantics

Engines count whole units and floor. `Support.fractional_quantity` must be
`UNSUPPORTED` or the engine refuses (`UnsupportedQuantitySemanticsError`) —
`UNKNOWN` is refused too. Unit nouns come from `ProductVocabulary`
(`contract` / `contract(s)`), so messages are identical for futures and would
read correctly for another product without branching.

## 10. Money / currency semantics

Decimal authority unchanged. No currency default anywhere; `quote_currency` is
`None` because no contract record carries one. The Phase 8 rule — a currency is
shown only when the user supplied it — is unchanged. AST-checked: no currency
code literal in `domain` or `application`.

## 11. Price / tick semantics

Price precision comes only from verified product metadata. AST-checked: no
`quantize` and no `Decimal("0.01")`-shaped constant in generic modules. An
unverified tick size yields `UNVERIFIED`, never an assumed cent grid.

## 12. Capabilities

`ProductCapabilities` over three-valued `Support`: margin, expiry, short selling,
fractional quantity, funding, open interest. Futures: margin/expiry/short/open
interest `SUPPORTED`; fractional quantity and funding `UNSUPPORTED` — properties
of the product type, no venue rule. No capabilities are defined for
unimplemented classes. A capability says what the product *type* can have; it
never says the fact is present or usable (see §31).

## 13. Generic analysis-core audit

Market, technical, structure, analysis, suitability, synthesis, vision and
presentation do not import the product policy or the futures risk entry points
(architecture.md rule 19). Analysis still accepts optional basis/open-interest
readings as it has since Phase 4.

## 14. Risk boundary

Generic: account equity, risk budget, direction, entry, stop, stop distance,
floor sizing, binding constraint, margin utilisation and leverage warnings,
gross/net P&L, what-if, risk/reward. Product-specific via policy: point value,
price grid, margin per unit, calculability, quantity semantics and vocabulary.
`app.domain.risk` cannot import `app.domain.futures` (architecture.md rule 17), and an AST
check forbids `.multiplier`, `.tick_size`, `.initial_margin`, `.contract` and
`.valuation` access in `risk` and `instrument`.

## 15. Phase 9 preparation

A future `PaperPosition` can hold an `InstrumentId` and a `ProductPolicy` and
compute through `size_for_product`, `pnl_for_product`, `assess_margin_for_product`
and `simulate_for_product`. No position type, lifecycle or state was created.

## 16. API compatibility

Additive only: `risk.contract.asset_class` and `asset_class_status`, present
only when a trusted contract record exists. All 10 Phase 8 API golden responses
match by digest after removing only these two keys and `generated_at`. No field
renamed or removed.

## 17. Frontend impact

zod schema and mapper accept the two optional fields (default `null`, so a
Phase 8 response still parses). The risk card shows "Varlık sınıfı: Vadeli işlem
sözleşmesi" only for a contract record, "(doğrulanmadı)" for an unverified
classification, raw status in Pro mode; an unimplemented value is shown raw,
never translated. A source scan confirms no selectable equity, crypto or FX
control. No redesign. Because no contract record carries a classification source
today (§31), a user currently always sees "(doğrulanmadı)" beside the class.

## 18. Golden / parity tests

*Accounting (reconciled in §31):* `test_futures_parity.py` collects **103 pytest
items** = 99 parametrised domain cases (one item each) + 1 item comparing all 10
API responses + 3 structural items (baseline provenance, no case added or
dropped, every sizing outcome and refusal type present).

Recorded by a one-off runner that refused to start unless `backend/app` was
unmodified against `1c3d555`; a second in-memory run matched the file exactly
(deterministic). 99 domain cases (all four sizing outcomes, 14 refusals across
`ContractValidationError`, `UnsupportedValuationModelError`,
`UnverifiedFinancialFactError`, `PnLInputError`) and 10 API cases (ALLOWED,
NOT_PERMITTED, UNDETERMINED, UNAVAILABLE, with and without a contract and
currency). `test_futures_parity.py`: **103 passed**. The case module later
changed only in formatting and in how cases are deferred (typed runners, §31);
the baseline file was never rewritten and its SHA-256 is unchanged.

## 19. Mutation probes

| Probe | Result |
| --- | --- |
| A. generic code hard-codes `FUTURES` | DETECTED |
| B. generic price assumes a 0.01 tick | DETECTED |
| B2. unverified tick treated as a cent grid | DETECTED |
| C. money assumes TRY | DETECTED |
| D. risk bypasses ProductPolicy (reads `.contract.multiplier`) | DETECTED |
| D2. risk imports the futures product | DETECTED (import contract broken) |
| E. futures policy changes multiplier semantics | DETECTED (parity) |
| F. unsupported asset falls back to futures | DETECTED |
| G. user-declared asset class becomes VERIFIED | DETECTED |
| H. Equity registered as implemented | DETECTED |
| H2. fake `EquityProductPolicy` with placeholder maths | DETECTED |
| I. capability `SUPPORTED` read as margin available | DETECTED (closeout) |
| J. unusable multiplier changes asset identity | DETECTED (closeout) |
| K. verified numbers establish the asset class | DETECTED (closeout) |
| L. unresolved symbol silently picks an available futures record | DETECTED (closeout) |

**15 of 15 detected.** All seven touched files restored byte-identically.

## 20. Phase 8 regression

Against the rebuilt containers: end-to-end through nginx **23/23**; adversarial
**16/16 held**; Vision separation browser probe — non-join stated, analysis
completes after a screenshot, `analysis_id` byte-identical to a direct API call;
accessibility — focus to result heading, 0 nameless controls, 0 undersized
targets, 0 contrast failures of 350, no horizontal scroll at 1280/640/390/320;
output bounds — response **530 676 → 530 021 bytes** at 1 200 → 2 500 rows,
identical to the Phase 8 closeout measurement.

## 21. Backend test count

**2 541 passed, 0 skipped** (PostgreSQL at `127.0.0.1:5432/viop_test`), after the
human-review closeout. The first report, before it, recorded 2 504.

## 22. Frontend test count

**274 passed** across 17 files.

## 23. Architecture contracts

**19 kept, 0 broken** (16 → 19): generic risk depends on no product; instrument
boundary depends only on common; analysis core does not depend on product
policies (matrix in §31). `instrument` added beneath analysis and suitability
in two existing contracts. Every new module imports cleanly first in a fresh interpreter.

## 24. Docker / runtime

`docker compose config -q` clean; backend and frontend rebuilt; all three
services healthy; served assets identical to the tested build.

## 25. Dependencies

None added, backend or frontend.

## 26. Security review

No API key, credential, `.env` content, local absolute path, debug logging,
uploaded file or browser artefact in the diff. No execution or broker code.
Two Phase 8 frontend files briefly showed as modified through a stale index stat
cache; resolved in §31 without changing content.

## 27. Known limitations

- Public result fields keep futures vocabulary (`allowed_contracts`,
  `loss_per_contract`, `max_contracts`) for API compatibility.
- Quantity is integral; fractional products are refused, not supported.
- `quote_currency` is always `None` until a contract record carries a verified
  currency.
- Venue is not modelled; symbol is the instrument key, as before.
- `ContractMetadataProvider` still returns futures records only.
- Basis and open-interest evidence remain futures readings passed as optional
  analysis inputs.

## 28. Future asset roadmap

VİOP futures is the reference implementation. Equities, crypto spot, crypto
perpetuals and FX each need verified work in metadata, quantity semantics, fees,
margin/leverage, sessions/calendars, settlement, lifecycle, market-data provider
and risk policy — tabulated by category, without invented values, in
`docs/architecture.md` ("The multi-asset boundary").

## 29. Phase-boundary verification

Mechanically searched `backend/app` and `frontend/src`: no `PaperPosition`,
paper-trade creation, journal, replay, backtest, `LiveMarketDataProvider`,
WebSocket/SSE, broker, order execution or Midas integration. **Phase 9: NOT
STARTED.**

## 30. Final git status

`main` at `1c3d555`, nothing staged, nothing committed, nothing pushed. 25
tracked files modified; new untracked files are the instrument package,
`futures/policy.py`, `futures/risk.py`, `tests/unit/multi_asset/`,
`multiAsset.test.tsx` and this report.

## 31. Human-review micro-closeout

**Asset-class provenance is not product calculability.** Confirmed coupled in
the first version (`_classification` read the multiplier and tick-size status)
and corrected. `FuturesContract` gained an optional `classification:
VerifiedValue[AssetClass]`; a present value must be `FUTURES` and is checked by
the existing provenance rules (an unsourced verified claim is blocking). The
policy reports that value as-is, or `UNVERIFIED` when absent. No record in the
system carries one, so no classification is reported as verified, and none was
fabricated. Existing calculations are unchanged: sizing with and without a
classification source is identical, and the golden baseline still matches.

**Capability is not availability.** Futures margin `SUPPORTED` coexists with a
`MISSING` margin and an unavailable margin calculation; expiry `SUPPORTED`
coexists with an `UNKNOWN` contract state. Capabilities are identical for every
metadata state, and the policy never reads its capabilities to answer an
availability question (AST-checked).

**Dispatch.** `FuturesProductPolicy` is constructed in exactly three places, each
from a `contract` record: the orchestrator's `_product_policy` (after the
`contract is None` check, on a record returned by the server-composed provider),
the API projection of that same record, and the four futures entry points. The
request schema has no asset-class, product, exchange or locale field; each is a
422. Future-looking, perpetual-looking, FX, equity and near-miss symbols with no
server record yield no policy. Policy selection follows the record type, not
the classification's status - gating existing calculations on a verified
classification would change VİOP outputs and is left for human decision.

**Vocabulary.** Four ASCII English domain terms used in the engine's English
audit reasons, unchanged since Phase 3; no localized prose in any policy. Kept.

**Parity accounting.** 103 items in `test_futures_parity.py`, as reconciled in
§18. The baseline file's SHA-256 was recorded at capture time as
`10e4f21405c3572b4a3b9df9880493270186fdeb50326985f1b5095b4b2d1851` and is
identical after every change in this closeout. The case module changed only in
how cases are *deferred* (typed runners replacing eight default-argument lambdas)
and in formatting; all 99 recorded results still match, which an altered input
would break.

**Type ignores.** 14 were Phase 8.5 additions. 11 removed: eight `[misc]` in the
golden module (typed `later(...)` runners) and three `[operator]` (engine thunks
typed as `Callable[[], object]`). Three remain, each deliberately doing what the
type forbids to prove the runtime also refuses it, each with a specific code and
a comment: writing to the read-only registry (`[index]`), constructing an
`InstrumentId` with no class (`[call-arg]`), passing a string as a class
(`[arg-type]`). No production-code ignore was added.

**Diff hygiene.** `cropRegion.ts` and `TechnicalPanel.tsx` were byte-identical to
`HEAD` (LF in both, empty numstat); they showed as modified only through a stale
index stat cache after prettier rewrote them. `git update-index --refresh`
cleared it without touching content or any other file. They are no longer part
of the diff.

**Contract accounting.** `HEAD` declares 16 contracts; Phase 8.5 adds 3; total
**19**. The earlier "contract 16 / 18" references were positions in
`docs/architecture.md`'s list, which had 15 entries for 16 contracts because the
Phase 8 orchestrator contract was never listed. That entry was added, so the
list and the configuration now both count 19.

| New contract | Forbids | Result |
| --- | --- | --- |
| The generic risk engine depends on no product implementation | `app.domain.risk` → `app.domain.futures` | KEPT |
| The instrument boundary depends only on common vocabulary | `app.domain.instrument` → futures, risk, analysis, technical, structure, market, suitability, synthesis, vision | KEPT |
| The analysis core does not depend on product policies | market, technical, structure, analysis, suitability, synthesis, vision, application synthesis/vision/presentation → `app.domain.futures.policy`, `app.domain.futures.risk` | KEPT |

**Mutation probes.** 15 of 15 detected, all touched files restored
byte-identically: the 11 earlier probes plus capability `SUPPORTED` read as
margin available, an unusable multiplier changing asset identity, verified
numbers establishing the class, and an unresolved symbol silently picking an
available futures record.

**Counts after the closeout.** Backend **2 541 passed, 0 skipped**; frontend
**274 passed**; **19** contracts kept; multi-asset suite 188 passed.

- Were Equity calculations implemented? **NO**
- Was Crypto trading implemented? **NO**
- Was FX trading implemented? **NO**
- Did existing Futures/VİOP semantics change? **NO**
- Was Paper Trading started? **NO**
- Any execution/broker capability? **NO**
