# VIOP AI MARKET INTELLIGENCE

## ULTIMATE MASTER BUILD SPECIFICATION

You are acting as a principal-level:

* quantitative software architect
* AI engineer
* backend engineer
* frontend engineer
* data engineer
* risk systems engineer
* trading systems engineer
* product designer
* QA engineer
* security engineer

Your task is to design and incrementally build a production-quality application called:

# VIOP AI MARKET INTELLIGENCE

This application is an:

**AI-assisted futures market analysis, education, risk-management, paper-trading, live-monitoring, market-replay and decision-support platform**

focused initially on:

**Borsa İstanbul VİOP**

and designed so that the architecture can later support additional exchanges and asset classes.

---

# 0. CORE PRODUCT VISION

This MUST NOT become a simplistic:

> upload chart → AI says BUY or SELL

application.

It must become a serious, explainable and measurable:

# MARKET DECISION INTELLIGENCE SYSTEM

The system should combine:

* structured OHLCV data
* futures contract metadata
* deterministic technical analysis
* market structure
* multi-timeframe analysis
* volume analysis
* VWAP
* support/resistance
* market regime detection
* futures-specific context
* spot/futures relationships
* liquidity analysis
* screenshot computer vision
* Claude Vision
* deterministic risk management
* position sizing
* scenario generation
* live setup monitoring
* alerts
* paper trading
* trade journaling
* market replay
* backtesting
* walk-forward testing
* shadow mode
* performance analytics
* explainability
* optional news/context intelligence
* future ML research

The main question the application should answer is NOT:

> "Will the market go up or down?"

Instead:

> "What is happening now?"

> "What evidence supports the bullish case?"

> "What evidence supports the bearish case?"

> "What market regime are we currently in?"

> "What conditions would make a LONG setup valid?"

> "What conditions would make a SHORT setup valid?"

> "What invalidates each setup?"

> "Where would the stop logically belong?"

> "What is the realistic risk/reward?"

> "Does this trade fit the user's account and risk limit?"

> "Should the user LONG, SHORT, WAIT or take NO TRADE?"

The system MUST be comfortable saying:

# WAIT

or:

# NO TRADE

when evidence is insufficient.

The objective is NOT to maximize signals.

The objective is to:

# MAXIMIZE DECISION QUALITY

while controlling:

* risk
* uncertainty
* false confidence
* overtrading
* bad data
* poor liquidity
* poor entries
* hindsight bias.

---

# 1. MOST IMPORTANT ARCHITECTURAL PRINCIPLE

The application must separate four major responsibilities:

## A. DETERMINISTIC ANALYTICAL ENGINE

Normal Python code must calculate:

* OHLCV transformations
* EMA
* SMA
* RSI
* MACD
* ATR
* VWAP
* ADX
* Bollinger Bands
* volatility
* returns
* volume metrics
* market structure
* swing highs/lows
* support/resistance
* risk/reward
* position sizing
* margin calculations
* P&L
* futures basis
* statistics
* backtest results
* strategy scoring
* empirical performance
* probability calibration.

Claude must NOT replace deterministic calculations.

---

## B. AI INTERPRETATION ENGINE

Claude should be responsible for:

* interpreting screenshots
* recognizing visible chart context
* explaining market structure
* comparing conflicting timeframe information
* synthesizing deterministic evidence
* explaining why an analysis changed
* creating beginner-friendly explanations
* identifying missing information
* producing Bull / Bear / Neutral scenarios
* creating a Devil's Advocate counterargument
* summarizing market context.

Claude is:

# AN INTERPRETATION AND EXPLANATION LAYER

Claude is NOT:

# THE SOURCE OF TRUTH FOR NUMERIC FINANCIAL CALCULATIONS

---

## C. RISK ENGINE

Risk calculations must be deterministic and independent of Claude.

Claude can explain risk.

Claude must never decide mathematical position size on its own.

---

## D. MARKET DATA ENGINE

The market-data layer must be vendor-independent.

Structured market data should always take priority over image-derived numbers.

Data priority:

1. trusted structured market data
2. user-confirmed values
3. validated contract metadata
4. screenshot-extracted values
5. Claude visual inference

Never replace verified structured data with a visual estimate.

---

# 2. SAFETY, RELIABILITY AND LANGUAGE RULES

Never claim:

* guaranteed profit
* guaranteed LONG
* guaranteed SHORT
* risk-free
* 100% bullish
* 100% bearish
* certain market prediction.

Use language such as:

* bullish bias
* bearish bias
* neutral
* conditional LONG
* conditional SHORT
* waiting for confirmation
* uncertain
* no trade.

Always distinguish:

# OBSERVED FACTS

from:

# CALCULATED METRICS

from:

# AI INFERENCES

from:

# SCENARIOS

from:

# RISKS.

Never fabricate:

* prices
* indicators
* volume
* open interest
* contract multiplier
* margin
* expiry
* tick size
* futures metadata
* market-data values.

If something cannot be verified:

mark:

# UNVERIFIED

or:

# MISSING DATA.

---

# 3. TARGET USER EXPERIENCE

This product must support both:

# BEGINNER MODE

and:

# PRO MODE

Beginner Mode is the default.

The application should be usable by someone who is new to:

* VİOP
* futures
* technical analysis
* leverage
* margin
* LONG / SHORT
* risk/reward
* position sizing.

The user should NOT need professional trading knowledge to understand what the system is saying.

At the same time, Pro Mode should expose enough information for advanced analysis.

---

# 4. TURKISH-FIRST UX

The initial version should prioritize:

# TURKISH

Technical terminology can show English secondarily.

Examples:

Destek — Support

Direnç — Resistance

Zarar Kes — Stop-Loss

Kâr Al — Take-Profit

Teminat — Margin

Kaldıraç — Leverage

Açık Pozisyon — Open Interest

Risk/Ödül — Risk/Reward

Kırılım — Breakout

Yeniden Test — Retest

The frontend must be prepared for i18n.

English can be added later.

---

# 5. BEGINNER MODE

Beginner Mode should prioritize:

* plain Turkish
* short explanations
* educational tooltips
* simple visual hierarchy
* clear warnings
* examples
* "Why?" explanations
* "What are we waiting for?" explanations.

Do NOT overwhelm the user with raw numbers.

Instead of:

EMA20 > EMA50 > EMA200
RSI 61.3
ADX 28

show:

> "The current trend favors buyers.

> Short-, medium- and long-term moving averages are aligned upward.

> Momentum remains positive.

> Trend strength is currently moderate."

Then provide:

# SHOW TECHNICAL DETAILS

for advanced metrics.

---

# 6. PRO MODE

Pro Mode should expose:

* all indicator values
* raw OHLCV
* market structure
* futures information
* volume metrics
* support/resistance strength
* regime statistics
* risk calculations
* setup scoring
* basis
* open interest
* liquidity
* confidence composition
* strategy configuration
* advanced charts.

---

# 7. EXPLAIN EVERYTHING

Every advanced concept should have an educational tooltip or information panel.

Cover at minimum:

* EMA
* SMA
* RSI
* MACD
* ATR
* VWAP
* ADX
* Bollinger Bands
* Volume
* Relative Volume
* Open Interest
* Basis
* Margin
* Leverage
* Risk/Reward
* Support
* Resistance
* Breakout
* Retest
* BOS
* CHOCH
* Market Regime
* Liquidity
* Spread.

Every educational explanation should answer:

## WHAT IS THIS?

## WHY DOES IT MATTER?

## HOW SHOULD I INTERPRET IT?

## WHAT SHOULD I NOT ASSUME?

Never teach simplistic rules such as:

> RSI > 70 = SELL.

Explain context and limitations.

---

# 8. TWO-LAYER ANALYSIS OUTPUT

Every market analysis must provide:

# LEVEL 1 — SIMPLE EXPLANATION

Example:

> "The broader trend remains upward.

> Price is currently approaching an important resistance area.

> Buying immediately may provide a poor entry unless the resistance is confirmed as broken.

> There is currently not enough evidence for a strong SHORT either.

> Current action:

> WAIT."

Then:

# LEVEL 2 — PROFESSIONAL ANALYSIS

Including:

* indicators
* metrics
* market structure
* timeframe analysis
* futures information
* support/resistance
* volume
* risk
* liquidity
* strategy state.

---

# 9. PRIMARY USER WORKFLOW

Typical workflow:

User selects:

ASELS futures

THYAO futures

XU030 futures

or another supported contract.

User enters:

Account Balance

Example:

2,500 TRY

Maximum acceptable risk:

Example:

75 TRY

or:

3%

User provides:

structured OHLCV data

and optionally:

1D screenshot

1H screenshot

15M screenshot

5M screenshot.

System processes:

Market Data
→ Data Validation
→ Technical Indicators
→ Market Structure
→ Market Regime
→ Multi-Timeframe Analysis
→ Futures Context
→ Evidence Fusion
→ Contradiction Detection
→ Setup Analysis
→ Risk Engine
→ Trade Suitability
→ Claude Interpretation
→ Beginner Explanation
→ Pro Analysis.

---

# 10. MULTI-TIMEFRAME ANALYSIS

Support:

1D
4H
1H
30M
15M
5M
1M

Default recommended hierarchy:

## 1D

Macro trend / primary regime

## 1H

Primary directional bias

## 15M

Setup formation

## 5M

Entry timing

Do NOT treat timeframes equally.

Example:

1D bullish

1H bullish

15M bearish correction

5M oversold

Do NOT simply average them.

Correct interpretation could be:

> "Higher timeframes remain bullish, but lower timeframes are correcting.

> Immediate LONG timing is weak.

> Wait for lower-timeframe stabilization."

---

# 11. DETERMINISTIC TECHNICAL ENGINE

Implement in Python.

At minimum calculate:

## TREND

EMA 9
EMA 20
EMA 50
EMA 200

SMA 20
SMA 50
SMA 200

---

## MOMENTUM

RSI 14

MACD 12 / 26 / 9

Optional:

Stochastic RSI.

---

## VOLATILITY

ATR 14

Bollinger Bands

Historical volatility.

---

## TREND STRENGTH

ADX 14.

---

## INTRADAY CONTEXT

VWAP

Architecture for Anchored VWAP later.

---

## VOLUME

Raw Volume

Volume Moving Average

Relative Volume

Volume Acceleration

Breakout Volume Confirmation

Volume Divergence

Time-of-Day Normalized Volume.

---

# 12. MARKET STRUCTURE ENGINE

Automatically detect:

* swing highs
* swing lows
* Higher High
* Higher Low
* Lower High
* Lower Low
* Break of Structure — BOS
* Change of Character — CHOCH
* consolidation
* range
* breakout
* breakdown
* failed breakout
* failed breakdown.

Avoid single-candle pattern dependence.

---

# 13. SUPPORT / RESISTANCE ENGINE

Use:

* swing highs
* swing lows
* repeated touches
* volume
* local pivots
* ATR-normalized distance
* clustering
* previous-day high
* previous-day low
* session high
* session low
* session open
* VWAP.

Prefer:

# ZONES

instead of unrealistic single exact values.

Example:

Support:

102.40 – 102.80

Resistance:

105.20 – 105.60.

Each zone should have:

strength_score

0–100.

Factors:

* touches
* recency
* reaction magnitude
* volume
* timeframe importance.

---

# 14. MARKET REGIME ENGINE

Classify:

STRONG_UPTREND

WEAK_UPTREND

RANGE

LOW_VOLATILITY_RANGE

HIGH_VOLATILITY_RANGE

WEAK_DOWNTREND

STRONG_DOWNTREND

BREAKOUT

BREAKDOWN

CHAOTIC

UNCERTAIN.

Use deterministic metrics:

* ADX
* ATR
* EMA structure
* market structure
* volatility
* range width
* momentum.

Strategy logic should be aware of the regime.

Example:

TREND regime
→ trend-following logic

RANGE regime
→ mean-reversion logic

BREAKOUT regime
→ breakout/retest logic

CHAOTIC
→ NO TRADE.

---

# 15. REGIME-SPECIFIC STRATEGY ROUTING

Do not use every strategy in every regime.

Architecture should support:

Trend Pullback

Breakout Retest

VWAP Reclaim

Momentum Breakout

Range Reversal

Mean Reversion

and future strategies.

A StrategyRouter should determine which strategies are relevant to the current regime.

---

# 16. EVIDENCE FUSION ENGINE

Create a central engine that combines evidence.

Example:

BULLISH EVIDENCE

+ 1H bullish trend

* EMA alignment

* Price above VWAP

+ 15M Higher Low

* Relative volume increasing

* Resistance breakout

NEGATIVE / BEARISH EVIDENCE

* Major resistance nearby

* RSI divergence

* Extended move.

Each evidence item should contain:

source

description

direction

strength

timeframe

reliability

timestamp.

Do NOT let Claude invent evidence.

Evidence should be generated primarily by deterministic engines.

---

# 17. CONTRADICTION ENGINE

Explicitly detect conflicting signals.

Example:

1D bullish

1H bullish

15M bearish

5M bearish

Output:

# TIMEFRAME CONFLICT

Explanation:

> "Higher-timeframe structure remains bullish, but lower-timeframe momentum is correcting.

> Immediate LONG timing may be poor."

Contradictions should reduce setup quality.

---

# 18. SETUP QUALITY

Initially do NOT call analysis scores:

"probability"

or:

"chance of winning."

Use:

# SETUP QUALITY

0–100.

Possible components:

Trend alignment

Market structure

Volume

Momentum

Support/resistance

Risk/reward

Timeframe alignment

Regime suitability

Liquidity

Data quality.

Show the breakdown.

Example:

Trend:
18/20

Structure:
17/20

Volume:
11/15

Momentum:
10/15

Risk/Reward:
14/15

Data quality:
12/15

Total:

82/100.

Weights must be configurable.

---

# 19. PROBABILITY RULE

Never show:

"83% chance of success"

unless the number is statistically justified.

Only introduce:

# EMPIRICAL PROBABILITY

after enough historical observations exist.

Example:

> Setup Quality 80–90 historically reached TP1 before Stop in X% of Y out-of-sample cases.

Any ML-generated probability must undergo:

* out-of-sample evaluation
* calibration
* reliability testing.

Until then:

use:

# SETUP QUALITY

not probability.

---

# 20. PROBABILITY CALIBRATION

Prepare architecture for:

Calibration Curves

Reliability Diagrams

Brier Score

Calibration Error

Probability Calibration.

Future models may use:

Isotonic Regression

Sigmoid / Platt-style calibration

or other validated methods.

The application should eventually answer:

> "When the model says 70%, does the event actually occur roughly 70% of the time?"

---

# 21. LONG SCENARIO ENGINE

Every analysis evaluates LONG independently.

Example:

# LONG SETUP

Status:

WAITING_FOR_CONFIRMATION

Setup Quality:

78/100

Entry zone:

105.20 – 105.50

Required confirmation:

* 15M candle closes above resistance
* relative volume > 1.4
* price remains above VWAP
* 5M retest holds.

Invalidation:

15M close below 103.80.

Stop:

103.70.

TP1:

107.20.

TP2:

109.50.

Risk/Reward:

TP1 = 1.3R

TP2 = 2.8R.

Explain why each condition matters.

---

# 22. SHORT SCENARIO ENGINE

Evaluate SHORT independently.

Example:

# SHORT SETUP

Status:

INACTIVE

Activation conditions:

* resistance rejection
* 15M close below VWAP
* Lower High confirmation
* selling volume increase.

Entry:

103.80

Stop:

105.30

TP1:

101.50

TP2:

99.20.

LONG and SHORT setup scores must NOT sum to 100.

Possible:

LONG:
35

SHORT:
38

Conclusion:

NO TRADE.

---

# 23. BULL / BEAR / NEUTRAL SCENARIOS

Every analysis should create:

# BULL CASE

# BEAR CASE

# NEUTRAL CASE

Explain:

What evidence supports each?

What would confirm it?

What would invalidate it?

The system should track which scenario is becoming more likely as market conditions change.

---

# 24. DEVIL'S ADVOCATE ENGINE

Claude must challenge the dominant analysis.

Example:

Primary thesis:

LONG

Counterarguments:

* major resistance nearby
* relative volume below average
* 5M momentum weakening
* possible divergence.

Create:

# STRONGEST ARGUMENT AGAINST THIS SETUP

This prevents confirmation bias.

---

# 25. NO TRADE ENGINE

Mandatory.

Reasons include:

CONFLICTING_TIMEFRAMES

LOW_VOLUME

LOW_LIQUIDITY

POOR_RISK_REWARD

MIDDLE_OF_RANGE

EXTENDED_MOVE

HIGH_VOLATILITY

LOW_VOLATILITY

NO_CONFIRMATION

INSUFFICIENT_DATA

EVENT_RISK

UNCLEAR_STRUCTURE

BAD_DATA

RISK_LIMIT_EXCEEDED.

Display:

# NO TRADE — WAIT

and explain:

# WHAT WOULD CHANGE THE DECISION?

---

# 26. ENTRY QUALITY ENGINE

Trend and entry quality must be separated.

Example:

Trend Quality:

89

Setup Quality:

82

Entry Quality:

51.

Possible conclusion:

> "Direction remains bullish, but current entry is poor."

Entry quality should consider:

* distance to support
* distance to resistance
* ATR extension
* VWAP distance
* recent candles
* retest state
* risk/reward
* momentum exhaustion.

---

# 27. FALSE BREAKOUT DETECTOR

Detect potential false breakouts.

Consider:

* breakout without volume
* failure to close above level
* rapid return under resistance
* VWAP loss
* weak retest
* long upper wick
* momentum failure.

Output:

# POSSIBLE FALSE BREAKOUT

Do not treat temporary level penetration as confirmed breakout.

---

# 28. RETEST ENGINE

Support:

BREAKOUT

→ WAIT

→ RETEST

→ HOLD

→ ENTRY QUALITY IMPROVES.

Beginner explanation:

> "The market broke resistance.

> We are waiting to see if the old resistance becomes support."

---

# 29. EXTENDED MOVE / FOMO PROTECTION

Create:

ExtendedMoveDetector.

Consider:

* VWAP distance
* EMA20 distance
* ATR-normalized extension
* consecutive directional candles
* RSI
* volume climax
* proximity to support/resistance
* volatility.

Possible output:

# EXTENDED MOVE WARNING

Example:

> "Trend remains bullish, but price is 2.4 ATR away from its short-term mean.

> Chasing the move may produce poor risk/reward.

> Waiting for pullback or retest is preferred."

Do NOT automatically SHORT an extended bullish market.

---

# 30. STOP QUALITY ENGINE

Stops should NOT be placed using arbitrary percentage rules.

Use:

* structural invalidation
* swing high/low
* support/resistance
* ATR
* volatility
* contract tick size.

Example:

> "Stop is placed below 184.20 because a close below this level breaks the current 5M Higher-Low structure."

Score stop quality.

---

# 31. FUTURES-SPECIFIC VİOP ENGINE

Create a FuturesContract domain model containing:

symbol

underlying_symbol

contract_name

expiry

contract_multiplier

tick_size

tick_value

initial_margin

maintenance_margin if available

settlement_type

trading_session

current_futures_price

current_spot_price

open_interest

volume

days_to_expiry.

Do NOT hard-code mutable contract values.

Use:

# ContractMetadataProvider.

If no live provider exists initially:

implement:

ManualContractMetadataProvider.

---

# 32. BASIS ANALYSIS

When spot and futures data exist calculate:

basis =
futures price - spot price

basis_percent

optional annualized basis.

Display:

futures premium

or:

futures discount.

Do not treat basis alone as directional prediction.

---

# 33. OPEN INTEREST ANALYSIS

If Open Interest exists:

Price ↑ + OI ↑
→ possible new long participation

Price ↑ + OI ↓
→ possible short covering

Price ↓ + OI ↑
→ possible new short participation

Price ↓ + OI ↓
→ possible liquidation.

Treat these as contextual interpretations.

Never absolute rules.

---

# 34. RELATIVE STRENGTH ENGINE

Compare:

underlying stock

vs

BIST30

BIST100

relevant sector index

and optionally peer group.

Example:

BIST30:
+0.4%

ASELS:
+2.1%

Result:

ASELS outperforming market.

Relative strength must be contextual evidence, not a standalone trade signal.

---

# 35. MARKET BREADTH

Prepare architecture to analyze:

advancing constituents

declining constituents

unchanged constituents

percentage above moving averages

new highs/lows

sector participation.

Especially useful for:

XU030

index futures.

---

# 36. LIQUIDITY INTELLIGENCE

Analyze when available:

bid

ask

spread

spread percentage

volume

open interest

depth

turnover

order-book quality.

Possible result:

# GOOD SETUP

but:

# TRADE BLOCKED — LOW LIQUIDITY.

Setup quality and tradability are separate concepts.

---

# 37. TIME-OF-DAY INTELLIGENCE

Classify:

OPENING_PHASE

EARLY_SESSION

MID_SESSION

LATE_SESSION

CLOSE_APPROACHING

EVENING_SESSION.

Volume and volatility should be interpreted relative to time-of-day.

For example:

compare 09:45 volume with typical 09:45 historical volume, not full-session volume.

---

# 38. SCREENSHOT / VISION ANALYSIS

Users should be able to upload:

PNG

JPEG

WEBP.

Preferred screenshot slots:

1D

1H

15M

5M.

Claude Vision may infer:

symbol

timeframe

visible trend

market structure

support/resistance

candlestick context

visible indicators

visible volume

user-drawn levels.

But:

Never blindly trust visually extracted numbers.

Each extracted value should include:

field

value

source

confidence.

Example:

{
"field": "RSI",
"value": 63.2,
"source": "screenshot",
"confidence": 0.74
}

If structured data says:

RSI = 61.27

structured value wins.

---

# 39. SCREENSHOT QUALITY VALIDATION

Before screenshot analysis determine:

* readable?
* symbol visible?
* timeframe visible?
* price scale visible?
* chart large enough?
* timestamp visible?
* indicators readable?
* screenshot cropped?
* screenshot stale?

Provide:

Screenshot Quality:
0–100.

If quality is poor:

do not produce false precision.

---

# 40. DATA QUALITY ENGINE

Data integrity is a first-class product feature.

Detect:

* missing candles
* duplicate candles
* out-of-order ticks
* impossible prices
* zero or negative price
* timestamp mismatch
* stale feed
* wrong contract
* expired contract
* wrong timeframe
* impossible OHLC relationship
* large suspicious spikes.

If data integrity fails:

# ANALYSIS BLOCKED

Do not allow Claude to create new live trade confirmations from bad data.

---

# 41. DATA QUALITY PANEL

Every analysis should display:

OHLCV:
100%

Contract Metadata:
100%

1H Screenshot:
93%

15M Screenshot:
87%

Open Interest:
Missing

News:
Not Connected

Overall Data Quality:
86%.

Never hide missing critical information.

---

# 42. RISK ENGINE

User enters:

account_balance

Example:

2,500 TRY.

Risk mode:

FIXED

Example:

75 TRY

or:

PERCENTAGE

Example:

3%.

Calculate:

risk_amount

stop_distance

loss_per_contract

maximum_contract_count

notional_exposure

effective_leverage

margin_usage

remaining_free_margin.

Example:

Account:

2,500 TRY

Max Risk:

75 TRY

Entry:

105

Stop:

104

Multiplier:

100.

Loss per contract:

100 TRY.

Conclusion:

# TRADE NOT ALLOWED

because one minimum contract exceeds configured risk.

This is mandatory.

---

# 43. SETUP QUALITY VS TRADE SUITABILITY

Always separate:

Setup Quality

Market Quality

Entry Quality

Data Quality

Risk Suitability

Liquidity Quality.

Example:

Setup:
91/100

Market:
88/100

Data:
97/100

Entry:
79/100

Risk Suitability:
31/100.

Conclusion:

# NO TRADE

even though technical setup is excellent.

---

# 44. MARGIN SAFETY

Display:

Account Equity

Used Margin

Free Margin

Margin Utilization %

Notional Exposure

Effective Leverage

Risk to Stop.

Show modeled account effect for reasonable adverse moves when possible.

Example:

-1%

-2%

-3%

-5%.

Display warnings:

HIGH MARGIN UTILIZATION

EXCESSIVE EFFECTIVE LEVERAGE

RISK LIMIT EXCEEDED.

---

# 45. ADAPTIVE RISK WARNINGS

Market volatility should affect warnings.

Possible behavior:

NORMAL_VOLATILITY
→ normal configured risk

HIGH_VOLATILITY
→ warning / reduced suggested risk

EXTREME_VOLATILITY
→ NO TRADE candidate.

Do not automatically modify real-money risk without user control.

---

# 46. P&L ENGINE

LONG:

PnL =
(exit - entry)
× multiplier
× contracts

SHORT:

PnL =
(entry - exit)
× multiplier
× contracts.

Support:

gross P&L

fees

commission

slippage

net P&L

return on account

return on margin

R multiple.

---

# 47. WHAT-IF SIMULATOR

Allow beginner user to preview:

"What happens if I enter one contract?"

Example:

Entry:
185.50

Price 187:
+X TRY

Price 186:
+X TRY

Price 185:
-X TRY

Price 184:
-X TRY.

Also show:

percentage of account gain/loss.

---

# 48. PRE-TRADE SIMULATOR

Before paper trade, show:

Account

Direction

Contracts

Entry

Stop

Targets

Maximum stop loss

Risk/account %

Potential TP1

Potential TP2

Risk/Reward

Margin usage

Effective leverage.

Then allow:

# START PAPER TRADE.

---

# 49. PRE-TRADE CHECKLIST

Before opening a paper trade verify:

Trend identified

Setup valid

Entry trigger confirmed

Stop defined

Risk calculated

Risk/Reward acceptable

Position size valid

Volume confirmation

No major contradiction

Data quality acceptable

Liquidity acceptable.

Each:

PASS

WARNING

FAIL.

Critical failures should produce:

# TRADE QUALITY INSUFFICIENT.

---

# 50. LIVE MARKET ARCHITECTURE

The application must be designed from the beginning for real-time market intelligence.

Do NOT assume analysis only occurs when the user presses:

ANALYZE.

Create:

# LiveMarketDataProvider

interface.

Possible future implementations may use legitimate licensed:

WebSocket

streaming

market-data providers.

Never scrape broker UIs or unofficial endpoints.

Never request broker passwords.

---

# 51. REAL-TIME ANALYSIS PIPELINE

Architecture:

Live Market Data
↓
Tick Normalizer
↓
Data Validator
↓
Candle Aggregator
↓
Indicator Engine
↓
Market Structure Engine
↓
Support/Resistance Engine
↓
Regime Engine
↓
Volume Engine
↓
Futures Engine
↓
Evidence Fusion
↓
Contradiction Engine
↓
Setup Engine
↓
Risk Engine
↓
Liquidity Guard
↓
Event Detector
↓
Claude Synthesis when appropriate
↓
Realtime Frontend
↓
Alerts
↓
Audit / Journal.

---

# 52. LIVE CANDLE MANAGEMENT

Maintain candles for:

1D

4H

1H

30M

15M

5M

1M.

Each candle must have:

FORMING

or:

CLOSED

state.

Never treat forming candle as equivalent to confirmed closed candle.

Display:

# INTRABAR SIGNAL

vs:

# CONFIRMED SIGNAL.

Example:

> "Price is currently above resistance, but the 15-minute candle has not closed.

> Breakout remains unconfirmed."

---

# 53. LIVE SETUP STATE MACHINE

Each opportunity has a state.

Possible states:

IDLE

WATCHING

SETUP_FORMING

WAITING_FOR_CONFIRMATION

TRIGGERED

RETESTING

ENTRY_AVAILABLE

ENTERED

TP1_REACHED

TP2_REACHED

STOPPED

INVALIDATED

CANCELLED

EXPIRED

NO_TRADE.

Every transition stores:

timestamp

previous_state

new_state

reason

market_snapshot.

---

# 54. LIVE SETUP CARD

Build a visible live card.

Example:

ASELS VİOP

STATE:

WAITING FOR CONFIRMATION

LONG QUALITY:

74 / 100

SHORT QUALITY:

31 / 100

Current Price:

184.90

Resistance:

185.50

Distance:

0.32%

VWAP:

Above

Volume:

Waiting

1H Trend:

Bullish

15M Structure:

Bullish

5M Momentum:

Weak

Candle Close:

04:18 remaining.

Then:

LONG requirements:

✓ 1H bullish

✓ price above VWAP

✓ bullish structure

✗ resistance breakout

⚠ volume confirmation.

---

# 55. WHAT ARE WE WAITING FOR?

When status is:

WAIT

or:

NO TRADE

show:

# WHAT ARE WE WAITING FOR?

Example:

1. 15M close above 185.50
2. Relative Volume > 1.3
3. VWAP remains supported
4. 5M retest succeeds

Progress:

2 / 4.

Update live.

This feature is especially important in Beginner Mode.

---

# 56. LIVE ANALYSIS TIMELINE

Maintain an immutable timeline.

Example:

10:15
1H bullish structure confirmed.

10:30
Price entered resistance zone.

10:42
Relative volume increased.

10:45
15M candle closed above resistance.

10:45
LONG quality changed 68 → 82.

10:50
Retest confirmed.

The user must understand:

WHAT changed

WHEN

WHY.

---

# 57. ANALYSIS CHANGE EXPLANATION

Whenever:

bias

regime

setup quality

state

risk status

changes materially:

show:

# WHY DID THE ANALYSIS CHANGE?

Example:

LONG quality:

79 → 51.

Reasons:

* Price lost VWAP.
* 5M structure changed HH/HL → LH/LL.
* breakout volume faded.
* resistance rejection appeared.

Never silently change analysis.

---

# 58. LIVE CONTRADICTION MONITOR

Continuously evaluate timeframe alignment.

Example:

1D:
BULLISH

1H:
BULLISH

15M:
BEARISH

5M:
BEARISH.

Output:

# TIMEFRAME CONFLICT.

---

# 59. LIVE RISK GUARD

Continuously check risk compatibility.

Example:

Account:
2,500 TRY

Max Risk:
75 TRY

Entry:
185.60

Stop:
184.45

Loss per contract:
115 TRY.

Result:

# POSITION SIZE INVALID.

The system must be able to say:

# GOOD SETUP

BUT:

# NOT SUITABLE FOR THIS ACCOUNT.

---

# 60. LIVE PAPER POSITION MONITOR

Display:

Entry

Current Price

PnL

PnL %

R Multiple

Stop

Targets

Distance to Stop

Distance to Target

Account Equity

Used Margin

Free Margin

Effective Leverage.

---

# 61. ALERT ENGINE

Possible alerts:

SETUP_FORMING

TRIGGER_APPROACHING

BREAKOUT_DETECTED

BREAKOUT_CONFIRMED

RETEST_DETECTED

ENTRY_AVAILABLE

VWAP_LOST

SUPPORT_BROKEN

RESISTANCE_BROKEN

TIMEFRAME_CONFLICT

VOLUME_CONFIRMATION

SETUP_INVALIDATED

STOP_APPROACHING

TP1_REACHED

TP2_REACHED

EXTENDED_MOVE

RISK_LIMIT_EXCEEDED

DATA_STALE.

Initially:

in-app alerts.

Prepare interfaces for future:

desktop notification

email

Telegram

Discord

mobile push.

Do not implement unnecessary integrations in MVP.

---

# 62. REALTIME FRONTEND TRANSPORT

Prefer:

WebSocket

or:

Server-Sent Events

where appropriate.

Do not use inefficient high-frequency polling.

Handle:

reconnect

heartbeat

temporary disconnect

stale connection

duplicate events

event ordering.

---

# 63. STALE DATA PROTECTION

Track:

last_tick_timestamp

last_candle_timestamp

provider_status

latency.

If market data becomes stale:

display:

# LIVE DATA STALE.

Do not generate new live confirmations until fresh data returns.

---

# 64. EVENT-DRIVEN CLAUDE USAGE

Never call Claude on every tick.

Deterministic engines update continuously.

Claude should be invoked when:

* candle closes
* structure changes
* resistance breaks
* support breaks
* VWAP crosses
* setup triggers
* setup invalidates
* meaningful contradiction appears
* significant risk change occurs
* user explicitly requests explanation
* scheduled analytical checkpoint occurs.

This reduces:

latency

cost

noise

instability.

---

# 65. SCREENSHOT / LIVE DATA RELATIONSHIP

Claude Vision is supplementary.

Preferred hierarchy:

Market Data
→ Deterministic Analysis
→ Screenshot Context
→ Claude Synthesis.

Never make screenshot AI the sole source of numeric truth when structured data exists.

---

# 66. EVIDENCE LEDGER

Every analysis decision should record evidence.

Example:

Timestamp:

10:45

LONG Quality:

82

Bullish Evidence:

* EMA alignment
* VWAP hold
* breakout
* volume confirmation
* 1H trend.

Negative Evidence:

* major resistance proximity.

Store:

evidence

source

timeframe

timestamp

strength

data version.

---

# 67. AUDITABILITY

Store:

input-data version

analysis timestamp

screenshots

indicator values

model name

Claude prompt version

Claude response

risk calculation

strategy version

market regime

setup state

state transition reason.

The system should answer:

> "What exactly did the application know at this moment?"

---

# 68. CLAUDE STRUCTURED OUTPUT

Claude must not return arbitrary backend-driving free text.

Create strict Pydantic schemas.

Example conceptual schema:

AnalysisResult

fields:

symbol

timestamp

data_quality

market_regime

timeframes

support_zones

resistance_zones

bull_case

bear_case

neutral_case

long_scenario

short_scenario

no_trade_reason

contradictions

missing_information

risk_analysis

summary

beginner_explanation.

Claude output must be validated.

Invalid AI output must never directly control application state.

---

# 69. AI PROVIDER ARCHITECTURE

Create:

AIProvider

interface.

Initial adapter:

ClaudeAIAdapter.

Support:

text

images

structured output

timeouts

retries

rate limits

token usage tracking

error handling.

Never scatter Anthropic API calls through business logic.

---

# 70. INTERNAL CLAUDE SYSTEM PROMPT

Create a maintainable system prompt resembling:

"You are a financial market analysis synthesis engine.

You do not predict markets with certainty.

You receive deterministic metrics, market structure, futures metadata, risk calculations, chart-analysis findings and contextual evidence.

Never invent numeric information.

Structured validated data overrides screenshot inference.

Always distinguish fact from inference.

Evaluate LONG and SHORT separately.

Consider Bull, Bear and Neutral cases.

Generate a Devil's Advocate counterargument.

Prefer WAIT or NO TRADE when confirmation is insufficient.

Never override deterministic risk limits.

Never recommend position size beyond the Risk Engine's output.

Explain invalidation.

Explain why analysis changed.

Focus on scenario planning rather than prediction."

Version the prompt.

---

# 71. NEWS / CONTEXT ARCHITECTURE

Create:

NewsProvider

interface.

Do not make a specific vendor mandatory.

Possible future analysis:

company news

macro data

central bank events

earnings

sector developments

index-level events.

Classify:

positive

negative

neutral

high-impact

low-impact.

News sentiment must never create a trade by itself.

---

# 72. EVENT RISK

Support:

earnings dates

interest-rate decisions

inflation releases

major disclosures

configured market events.

Possible rule:

do not initiate new paper trades X minutes before a configured high-impact event.

---

# 73. DATA PROVIDER ARCHITECTURE

Create interfaces:

MarketDataProvider

LiveMarketDataProvider

HistoricalMarketDataProvider

ContractMetadataProvider

NewsProvider

AIProvider

ScreenshotAnalyzer

StorageRepository.

Initial implementations can include:

CSVMarketDataProvider

MockMarketDataProvider

HistoricalCSVProvider

ManualContractMetadataProvider

ClaudeAIAdapter.

Do not depend initially on a specific broker.

---

# 74. LIVE / REPLAY / BACKTEST PARITY

The same deterministic engines must be used by:

LIVE MODE

MARKET REPLAY

BACKTEST

SHADOW MODE.

Do NOT create separate analysis logic for each.

Only the data provider should change.

Example:

HistoricalMarketDataProvider

or:

LiveMarketDataProvider

feeds the same downstream engine.

This is critical.

---

# 75. PAPER TRADING

Do NOT integrate real broker execution initially.

Build paper trading first.

Allow:

OPEN PAPER LONG

OPEN PAPER SHORT.

Store:

entry timestamp

entry price

contracts

stop

targets

strategy

analysis snapshot

market regime

setup quality.

Support:

partial take-profit

manual close

move stop to breakeven

stop-loss

take-profit.

---

# 76. TRADE JOURNAL

Automatically record paper trades.

Store:

symbol

contract

direction

entry

exit

contracts

stop

targets

setup

reason

screenshots

analysis snapshot

market regime

setup quality

entry quality

risk suitability

PnL

R multiple

holding time

user notes

result

whether plan was followed.

---

# 77. PERFORMANCE ANALYTICS

Show:

total trades

win rate

average R

expectancy

profit factor

maximum drawdown

average winner

average loser

largest winner

largest loser

LONG performance

SHORT performance

symbol performance

timeframe performance

regime performance

strategy performance

setup-quality performance

entry-quality performance.

Do not emphasize win rate alone.

Expectancy is more important.

---

# 78. SHADOW MODE

Before relying on the system:

run:

# SHADOW MODE.

System analyzes live market data but opens no paper or real position automatically.

Record:

signal

entry candidate

stop

targets

setup quality

market regime

later outcome.

Use this to evaluate system behavior under live conditions.

---

# 79. MARKET REPLAY

Implement historical candle-by-candle replay.

Future candles must remain hidden.

User can:

NEXT CANDLE.

AI and deterministic engines may only receive information that would have been available at that historical timestamp.

Store:

AI decision

user decision

actual later outcome.

Avoid hindsight bias.

---

# 80. LEARN MODE

Create educational mode.

Possible topics:

LONG / SHORT

candlesticks

trend

support/resistance

volume

EMA

RSI

VWAP

risk/reward

margin

leverage

market structure.

Example workflow:

show historical chart

ask:

"What is the current trend?"

User answers.

System explains:

correct / incorrect

and why.

Then:

"Would you LONG, SHORT or WAIT?"

Goal:

make the user progressively less dependent on AI.

---

# 81. BACKTEST ENGINE

Implement robust historical testing.

Mandatory:

NO LOOKAHEAD BIAS

NO FUTURE LEAKAGE.

Include:

commission

fees

spread

slippage

contract multiplier

signal timing

entry timing

stop logic

target logic

position sizing

liquidity assumptions.

Support:

historical test

walk-forward analysis

out-of-sample evaluation.

Never optimize on the full dataset.

---

# 82. WALK-FORWARD VALIDATION

For future predictive models or strategies use chronological validation.

Example concept:

Train:
2022

Test:
2023

then:

Train:
2022–2023

Test:
2024

then:

Train:
2022–2024

Test:
2025.

Avoid random train/test splitting for time series when it leaks future information.

---

# 83. TRANSACTION-COST REALISM

Backtesting must model:

commission

spread

slippage

latency assumptions

poor fills

liquidity.

Show:

THEORETICAL RESULT

and:

REALISTIC RESULT

when useful.

Do not optimize strategies on frictionless backtests.

---

# 84. STRATEGY CONFIGURATION

Strategies should be configurable rather than deeply hard-coded.

Example:

TrendPullbackStrategy

Conditions:

EMA20 > EMA50 > EMA200

price > VWAP

RSI between configured thresholds

15M Higher Low

5M structure break

relative volume > threshold.

Stop:

structure or ATR based.

Target:

minimum configurable R.

Use typed strategy configuration.

---

# 85. STRATEGY LEADERBOARD

Future analytics should compare:

Trend Pullback

Breakout Retest

VWAP Reclaim

Range Reversal

Momentum Breakout

etc.

Compare performance by:

market regime

symbol

timeframe

volatility

liquidity.

Do NOT automatically deploy the historical best-performing strategy without validation.

---

# 86. MACHINE LEARNING RULE

Do NOT add ML simply because this is an AI project.

First build strong deterministic baselines.

Only later evaluate whether ML adds real predictive value.

Potential future models:

XGBoost

LightGBM

time-series models

Transformer research.

Every ML model must be compared against baselines.

Require:

time-series split

walk-forward evaluation

out-of-sample testing

probability calibration

feature importance

SHAP when appropriate

transaction costs

slippage.

Reject models with no robust out-of-sample improvement.

---

# 87. FRONTEND

Use:

React

TypeScript strict

Vite

TanStack Query

Zod

professional financial charting library such as Lightweight Charts where appropriate.

Do not create a generic admin dashboard.

It should look like a serious financial workstation while remaining understandable.

---

# 88. VISUAL DESIGN

Default:

Dark Mode.

Professional.

Clean.

Dense but readable.

Avoid:

excessive gradients

crypto-casino aesthetic

unnecessary animations.

Semantic design:

Green:
Bullish / profit

Red:
Bearish / loss / danger

Amber:
Waiting / caution

Blue:
Neutral / information

Gray:
Inactive.

Do not rely only on color.

Use:

icons

labels

text

for accessibility.

---

# 89. MAIN SCREENS

Create:

## Dashboard

Watchlist

Active analyses

Live setup cards

Paper positions

Risk overview

Recent journal.

## Analyze Market

Contract selector

Account balance

Risk setting

Screenshot upload

OHLCV import.

## Analysis Workspace

Candlestick chart

timeframes

indicators

market structure

support/resistance

LONG

SHORT

WAIT

Risk

AI explanation.

## Live Monitor

Active setups

Live timeline

Alerts

Setup-state changes.

## Paper Trading

Positions

PnL

Stops

Targets.

## Journal

Historical trades.

## Replay

Historical candle-by-candle mode.

## Learn

Educational exercises.

## Backtest

Strategy selection

Parameters

Results.

## Performance

Analytics.

## Settings

Claude API

Risk defaults

Data providers

Contract metadata.

---

# 90. ANALYSIS WORKSPACE LAYOUT

Suggested layout:

LEFT:

Watchlist

CENTER:

Candlestick chart

RIGHT:

AI Market Intelligence

BOTTOM:

Risk and Trade Plan.

AI panel displays:

MARKET REGIME

MULTI-TIMEFRAME TREND

1D

1H

15M

5M

KEY SUPPORT

KEY RESISTANCE

LONG SETUP

SHORT SETUP

ENTRY QUALITY

NO TRADE

RISK

CONTRADICTIONS

MISSING DATA

DEVIL'S ADVOCATE.

---

# 91. SCREENSHOT UPLOAD UI

Provide slots:

1D

1H

15M

5M.

Features:

drag/drop

preview

replace

delete

crop

zoom

confirm inferred symbol

confirm inferred timeframe.

---

# 92. WHY ENGINE

Every major conclusion must answer:

WHY bullish?

WHY bearish?

WHY LONG?

WHY SHORT?

WHY WAIT?

WHY this support?

WHY this resistance?

WHY this stop?

WHY this target?

WHY this risk?

WHY this position size?

WHY did the score change?

Never show unexplained scores.

---

# 93. DATABASE

Create models for concepts such as:

UserSettings

Instrument

FuturesContract

MarketSnapshot

Candle

Screenshot

Analysis

TimeframeAnalysis

SupportResistanceZone

Evidence

Scenario

RiskCalculation

Setup

SetupStateTransition

Alert

PaperTrade

TradeEvent

JournalEntry

Backtest

BacktestResult

StrategyConfiguration

MarketRegime

AIAnalysisVersion.

---

# 94. BACKEND ARCHITECTURE

Use clean / hexagonal architecture.

Recommended:

Python 3.12+

FastAPI

Pydantic v2

SQLAlchemy 2

Alembic

PostgreSQL.

Suggested structure:

backend/
app/
domain/
market/
technical/
structure/
futures/
risk/
strategies/
setups/
trading/
backtest/
application/
services/
use_cases/
dto/
ports/
market_data.py
live_market_data.py
contract_metadata.py
ai.py
news.py
storage.py
adapters/
ai/
market_data/
persistence/
news/
api/
routes/
schemas/
websocket/
core/
config.py
logging.py
tests/

Domain code must NOT depend directly on:

FastAPI

SQLAlchemy

Anthropic SDK

HTTP clients.

---

# 95. DEPENDENCY DIRECTION

Domain

must remain independent.

Application

depends on Domain.

Adapters

implement Ports.

API

calls Application.

External infrastructure must never leak into core financial domain logic.

---

# 96. API DESIGN

Possible endpoints:

POST /api/analysis

GET /api/analysis/{id}

POST /api/analysis/{id}/screenshots

POST /api/market-data/import

GET /api/contracts

POST /api/risk/calculate

POST /api/paper-trades

PATCH /api/paper-trades/{id}

GET /api/journal

POST /api/backtests

GET /api/backtests/{id}

GET /api/performance

GET /api/health

GET /api/live/status.

Use typed OpenAPI schemas.

---

# 97. OBSERVABILITY

Implement structured logging.

Track:

API errors

AI latency

AI token usage

market-data provider failures

analysis duration

vision failures

backtest duration

WebSocket disconnects

stale data

state transitions.

Create:

health endpoint.

Prepare architecture for OpenTelemetry.

Do not over-engineer MVP.

---

# 98. SECURITY

Never log:

API keys

credentials

tokens.

Use:

environment variables.

Create:

.env.example.

Never commit:

.env

or:

secrets.

Validate uploads.

Limit file size.

Validate MIME types.

Sanitize filenames.

Secure temporary storage.

Do not store broker credentials.

---

# 99. TESTING

Testing is mandatory.

Backend:

pytest

pytest-asyncio.

Frontend:

appropriate React testing stack.

Test at minimum:

LONG PnL

SHORT PnL

position sizing

risk limits

margin logic

EMA

RSI

ATR

VWAP

ADX

support/resistance

market structure

regime classification

evidence fusion

contradiction detection

NO TRADE

entry quality

extended move

false breakout

data-quality validation

AI schema validation

paper-trading lifecycle

state machine

market replay

backtest no-lookahead behavior.

Financial calculations require especially strong tests.

---

# 100. CODE QUALITY

Use:

Ruff

mypy strict

pytest

TypeScript strict

ESLint

Prettier.

Avoid:

Any

untyped dictionaries

loosely typed financial calculations

unvalidated AI output.

---

# 101. DOCKER

Create:

backend Dockerfile

frontend Dockerfile

docker-compose.yml.

Initial services:

frontend

backend

postgres.

Only add Redis if actual architecture requires it.

Do not add infrastructure merely to look sophisticated.

---

# 102. DOCUMENTATION

Create an excellent README covering:

purpose

architecture

features

limitations

development setup

Claude API

Docker

testing

market-data architecture

risk engine

Beginner Mode

Pro Mode

paper trading

live monitoring

replay

backtesting

roadmap.

Explicitly state:

This is an analytical, educational and decision-support tool.

It does not guarantee financial outcomes.

---

# 103. PROJECT PHASES

Do NOT implement everything at once.

Follow strict phases.

---

# PHASE 0 — FOUNDATION

Implement:

repository structure

architecture boundaries

backend skeleton

frontend skeleton

database

Docker

configuration

health endpoint

testing infrastructure

logging

README

core interfaces / ports.

Do NOT implement advanced analysis.

Validate.

STOP.

---

# PHASE 1 — MARKET DATA + TECHNICAL ENGINE

Implement:

OHLCV

data validation

EMA

SMA

RSI

MACD

ATR

VWAP

ADX

Bollinger Bands

volume metrics

tests.

STOP.

---

# PHASE 2 — MARKET STRUCTURE + REGIME

Implement:

swings

HH

HL

LH

LL

BOS

CHOCH

support/resistance

regime classification

false breakout primitives

retest primitives

tests.

STOP.

---

# PHASE 3 — FUTURES + RISK

Implement:

FuturesContract

contract metadata

multiplier

tick size

margin

LONG PnL

SHORT PnL

basis

open-interest context

position sizing

risk limits

risk/reward

margin safety

what-if simulation

tests.

STOP.

---

# PHASE 4 — MULTI-TIMEFRAME + EVIDENCE

Implement:

1D

1H

15M

5M

timeframe roles

evidence fusion

contradictions

setup quality

entry quality

NO TRADE engine

Bull/Bear/Neutral scenarios.

STOP.

---

# PHASE 5 — BEGINNER / PRO EXPERIENCE

Implement:

Beginner Mode

Pro Mode

simple explanations

technical details

educational tooltips

Why Engine

Pre-Trade Checklist.

STOP.

---

# PHASE 6 — CLAUDE VISION

Implement:

image upload

screenshot quality

Claude Vision adapter

structured extraction

confidence

correction workflow

AI schemas.

Claude must not replace deterministic calculations.

STOP.

---

# PHASE 7 — CLAUDE SYNTHESIS

Combine:

technical data

market structure

regime

futures

risk

vision

evidence

into:

LONG

SHORT

WAIT

NO TRADE

Bull Case

Bear Case

Neutral Case

Devil's Advocate.

STOP.

---

# PHASE 8 — PROFESSIONAL ANALYSIS UI

Implement:

Dashboard

Analyze Market

Analysis Workspace

charts

risk panel

scenario cards

Beginner / Pro UI.

STOP.

---

# PHASE 9 — PAPER TRADING

Implement:

paper positions

state lifecycle

PnL

stop

targets

partial exits

trade events.

STOP.

---

# PHASE 10 — JOURNAL + PERFORMANCE

Implement:

trade journal

analytics

expectancy

profit factor

drawdown

performance breakdowns.

STOP.

---

# PHASE 11 — MARKET REPLAY + LEARN MODE

Implement:

historical replay

future-data isolation

interactive education

user-vs-system decisions.

STOP.

---

# PHASE 12 — BACKTEST

Implement:

historical strategy testing

transaction costs

slippage

no lookahead

walk-forward evaluation.

STOP.

---

# PHASE 13 — LIVE ARCHITECTURE

Implement:

LiveMarketDataProvider

mock streaming provider

WebSocket/SSE

candle aggregation

live state machine

stale-data protection

live setup card

live timeline

alerts.

Do NOT require paid real market data yet.

STOP.

---

# PHASE 14 — SHADOW MODE

Run live analysis without executing trades.

Collect real-time signal outcomes.

Create evaluation tools.

STOP.

---

# PHASE 15 — EXTERNAL DATA PROVIDERS

Only after architecture is stable:

evaluate legitimate providers for:

live market data

contract metadata

open interest

news

market breadth.

Integrate through adapters.

Do not couple domain logic to one vendor.

---

# PHASE 16 — ADVANCED RESEARCH

Only after deterministic baseline is validated:

ML

probability calibration

strategy leaderboard

portfolio risk

market breadth

order book

volume profile

CVD

advanced derivatives analytics

Monte Carlo

etc.

---

# 104. CRITICAL IMPLEMENTATION GATES

At the end of EVERY phase report:

# PHASE REPORT

## Implemented

## Partial

## Not Started

## Files Created

## Files Modified

## Commands Run

## Tests Run

## Test Results

## Architecture Decisions

## Known Limitations

## Technical Debt

## Next Phase.

Never claim a phase complete without:

working implementation

and:

passing validation.

---

# 105. NO FAKE IMPLEMENTATION

Do not create empty placeholder systems and mark them complete.

Use statuses:

IMPLEMENTED

PARTIAL

NOT STARTED.

Do not use TODO-heavy scaffolding as evidence of completion.

---

# 106. NO UNCONTROLLED SCOPE EXPANSION

Do not add libraries, databases, queues, brokers, cloud infrastructure or ML models merely because they are popular.

Every dependency must have a reason.

Prefer simplicity where possible.

Production quality does NOT mean maximum complexity.

---

# 107. FIRST USEFUL VERTICAL SLICE

The first useful end-to-end scenario should eventually support:

User enters:

Account:
2,500 TRY

Max Risk:
75 TRY

Contract metadata

OHLCV data

Screenshots:
1H
15M
5M

↓

Data Validator

↓

Technical Engine

↓

Market Structure

↓

Regime Engine

↓

Evidence Fusion

↓

Risk Engine

↓

Claude Vision

↓

AI Synthesis

↓

result.

Example:

# MARKET INTELLIGENCE

Trend:
Bullish

Current State:
Pullback

LONG:
WAITING FOR CONFIRMATION

SHORT:
LOW QUALITY

Action:
WAIT

Required trigger:

15M close above X

with:

volume confirmation.

Stop:

Y.

TP1:

Z.

Maximum contracts:

N.

Risk:

XX TRY.

Every conclusion must be explainable.

---

# 108. FINAL ANALYSIS FORMAT

Every complete analysis should follow:

# MARKET INTELLIGENCE

Instrument:

Contract:

Timestamp:

Data Quality:

## Simple Explanation

## Market Regime

## Multi-Timeframe Structure

1D:

1H:

15M:

5M:

## Trend

## Momentum

## Volume

## VWAP

## Market Structure

## Support Zones

## Resistance Zones

## Futures Context

Spot:

Futures:

Basis:

Open Interest:

Liquidity:

## Relative Strength

## Bullish Evidence

## Bearish Evidence

## Contradictions

## Devil's Advocate

# BULL CASE

# BEAR CASE

# NEUTRAL CASE

# LONG SCENARIO

Status:

Setup Quality:

Entry Quality:

Trigger:

Entry:

Stop:

TP1:

TP2:

Risk/Reward:

Invalidation:

# SHORT SCENARIO

Status:

Setup Quality:

Entry Quality:

Trigger:

Entry:

Stop:

TP1:

TP2:

Risk/Reward:

Invalidation:

# RISK

Account:

Max Risk:

Contracts Allowed:

Margin Usage:

Notional Exposure:

Effective Leverage:

# TRADE SUITABILITY

Setup Quality:

Risk Suitability:

Liquidity Quality:

Data Quality:

# FINAL STATE

LONG

SHORT

WAIT

or:

NO TRADE.

# WHY?

# WHAT ARE WE WAITING FOR?

# WHAT WOULD CHANGE THE DECISION?

These sections are mandatory.

---

# 109. BEGINNER SAFETY

If a beginner attempts:

very high leverage

high margin utilization

no stop

poor risk/reward

large account risk

show a plain-language warning.

Example:

> "Your configured stop would result in approximately 12% account loss.

> On a 2,500 TRY account this is approximately 300 TRY.

> This is substantially higher than your configured normal trade risk."

Never hide critical risk information inside Pro Mode.

---

# 110. LIVE TRADING RESTRICTION

Do NOT integrate automated real-money broker execution during early phases.

Do NOT:

scrape Midas

browser automate Midas

request Midas credentials

automatically place live trades.

Initial focus:

analysis

education

paper trading

market replay

backtesting

shadow mode.

Any future broker integration must require:

official supported broker API

explicit user authorization

separate safety review

clear trade confirmation design.

---

# 111. ADVANCED FUTURE ROADMAP

Prepare architecture for later:

real-time licensed WebSocket feed

mobile application

desktop notifications

Telegram

Discord

email alerts

portfolio-level risk

correlation analysis

market breadth

order book

volume profile

CVD

advanced futures analytics

economic calendar

news sentiment

ML signal research

Monte Carlo risk

multi-exchange support.

Do not implement prematurely.

---

# 112. PROJECT SUCCESS CRITERIA

This project is successful when:

1. Beginner users understand what the system is saying.

2. Advanced users can inspect technical details.

3. Financial calculations are deterministic and tested.

4. Claude never fabricates required numeric data.

5. The system can say NO TRADE.

6. The system distinguishes good setup from good trade.

7. Risk limits can block otherwise attractive setups.

8. Forming candles are distinguished from closed candles.

9. Live analysis changes are explainable.

10. Replay, backtest and live modes use the same analysis engine.

11. Historical testing prevents future leakage.

12. Performance is measured empirically.

13. Probability is never claimed without statistical justification.

14. The system is auditable.

15. Architecture can accept a licensed live-data provider later without major refactoring.

---

# 113. MOST IMPORTANT PRODUCT PRINCIPLE

The application's job is NOT:

> "Predict the next candle."

The application's job is:

# CREATE A DISCIPLINED, EXPLAINABLE, DATA-DRIVEN AND RISK-CONTROLLED DECISION PROCESS.

A correct output can be:

# WAIT.

A correct output can be:

# NO TRADE.

Preventing poor trades is just as valuable as identifying attractive setups.

---

# 114. BEFORE WRITING CODE

First inspect:

* operating system
* Git
* current repository
* repository tree
* README
* existing architecture
* Python
* Node.js
* npm / package manager
* Docker
* available ports
* current environment variables
* existing tests
* database configuration.

If the repository is empty:

design clean architecture.

If the repository already contains code:

do NOT overwrite working architecture blindly.

---

# 115. FIRST RESPONSE REQUIRED

Before implementing code, produce:

# VIOP AI MARKET INTELLIGENCE — IMPLEMENTATION PLAN

It must include:

## 1. Product Summary

## 2. Architecture

## 3. Repository Structure

## 4. Domain Boundaries

## 5. Dependency Direction

## 6. Backend Design

## 7. Frontend Design

## 8. Database Model

## 9. Market Data Architecture

## 10. Live Data Architecture

## 11. Deterministic Technical Engine

## 12. Market Structure Engine

## 13. Market Regime Engine

## 14. Evidence Fusion Engine

## 15. Risk Engine

## 16. Futures Engine

## 17. Claude Vision Pipeline

## 18. Claude Synthesis Pipeline

## 19. Beginner / Pro Experience

## 20. Paper Trading Architecture

## 21. Replay Architecture

## 22. Backtest Architecture

## 23. Live Monitoring Architecture

## 24. Security

## 25. Testing Strategy

## 26. Key Technical Risks

## 27. Phase Plan

## 28. Phase 0 Exact Scope.

After producing the plan:

review it for internal inconsistencies.

Then implement ONLY:

# PHASE 0 — FOUNDATION.

Do not proceed to Phase 1 automatically.

---

# 116. PHASE 0 COMPLETION REPORT

When Phase 0 is finished, stop and provide:

# PHASE 0 REPORT

## Current Git State

## Files Created

## Files Modified

## Architecture Implemented

## Backend Status

## Frontend Status

## Database Status

## Docker Status

## Tests Added

## Validation Commands Run

## Validation Results

## Known Limitations

## Technical Debt

## Next Recommended Phase.

Do NOT commit.

Do NOT push.

Do NOT proceed beyond Phase 0 unless explicitly instructed.

---

# 117. FINAL DEVELOPMENT RULES

Throughout the project:

* inspect before changing
* test before claiming success
* preserve architectural boundaries
* prefer deterministic calculations
* keep Claude behind interfaces
* validate all AI output
* maintain auditability
* prioritize data quality
* prioritize risk management
* avoid unnecessary complexity
* never fabricate implementation status
* never hide failures
* never commit or push unless explicitly instructed
* never jump ahead of the approved phase
* keep the product understandable for beginners
* preserve professional depth for advanced users.

---

# START NOW

Inspect the CURRENT repository and development environment.

Do not create files yet.

First produce:

# VIOP AI MARKET INTELLIGENCE — IMPLEMENTATION PLAN

based on what actually exists.

After verifying the plan for consistency:

implement ONLY:

# PHASE 0 — FOUNDATION

Validate it thoroughly.

Then STOP and report the results.
 

---------

# 118. EXTERNAL FINANCIAL FACT VERIFICATION RULE

Any exchange-specific, broker-specific, contract-specific or market-specific fact that may change over time MUST NOT be assumed from model memory.

This includes, but is not limited to:

* contract multiplier
* contract size
* tick size
* tick value
* trading session hours
* evening session eligibility
* initial margin
* maintenance margin
* active contracts
* expiration dates
* expiry rules
* settlement method
* physical vs cash settlement
* symbol format
* market-data fields
* commission assumptions
* exchange rules
* broker API capabilities
* available instruments
* live-data availability.

Whenever such information becomes necessary during development:

1. Prefer current official documentation from Borsa İstanbul or another authoritative primary source.

2. For provider-specific functionality, prefer the provider's official API/documentation.

3. Clearly distinguish:

   VERIFIED_CURRENT_FACT

   DEVELOPMENT_DEFAULT

   TEST_FIXTURE

   MOCK_DATA

   UNVERIFIED

4. Never silently hard-code mutable exchange specifications into domain logic.

5. Changing contract information must be accessed through:

   ContractMetadataProvider

   or an equivalent typed provider/configuration abstraction.

6. Tests may use explicit fixture values, but fixtures must be clearly identified as test data and must NEVER be presented as current exchange specifications.

7. If authoritative current information cannot be obtained, mark the value:

   UNVERIFIED

   instead of guessing.

8. Never use an LLM's remembered financial-market specification as the final source of truth.

9. When two authoritative sources conflict:

   * record the conflict
   * prefer the more recent applicable official source
   * do not silently choose a value
   * preserve auditability.

10. Financial correctness takes priority over implementation convenience.

Example:

Do NOT assume:

"one futures contract always represents 100 units"

because contract multipliers depend on the instrument.

Contract math must use verified contract metadata.

---

# 119. MANDATORY HUMAN APPROVAL GATE

After completing ANY development phase:

# STOP IMMEDIATELY.

Do NOT begin implementation of the next phase.

Do NOT create files for the next phase.

Do NOT scaffold the next phase.

Do NOT install dependencies solely required by the next phase.

Do NOT perform refactors whose only purpose is to prepare the next phase.

Do NOT interpret successful tests as authorization to continue.

At the end of EVERY phase:

1. Complete only the currently approved phase.

2. Run all relevant:

   * unit tests
   * integration tests where applicable
   * static analysis
   * type checking
   * linting
   * build validation
   * architecture validation.

3. Produce the required PHASE REPORT.

4. Explicitly list:

   IMPLEMENTED

   PARTIAL

   NOT STARTED

5. Report:

   * files created
   * files modified
   * commands executed
   * tests executed
   * test results
   * validation results
   * known limitations
   * technical debt
   * architecture decisions
   * unexpected issues
   * next recommended phase.

6. Clearly state what the NEXT phase would contain.

7. Then:

# STOP.

The next phase may begin ONLY after an explicit user instruction such as:

"Proceed to Phase 1."

or:

"Phase 0 is approved. Proceed to Phase 1."

Silence is NOT approval.

Passing tests is NOT approval.

Completion of the current phase is NOT approval.

A recommendation to continue is NOT approval.

If the user requests corrections after reviewing a completed phase:

remain inside the CURRENT phase.

Implement the requested corrections.

Run validation again.

Produce an updated Phase Report.

Then STOP again.

Never combine multiple phases into one implementation pass unless the user explicitly authorizes that exact combination.

Never silently expand the scope of an approved phase.

If a discovered architectural problem affects future phases:

document it.

Fix it now only if necessary for correctness of the CURRENT phase.

Otherwise record it as future work.

The purpose of this gate is:

IMPLEMENT
→ TEST
→ REPORT
→ REVIEW
→ APPROVE
→ NEXT PHASE.

Not:

IMPLEMENT
→ KEEP BUILDING AUTOMATICALLY.

---

# 120. INITIAL EXECUTION MODE — ANALYSIS AND SIGNALS ONLY

For the initial product and all currently approved development phases:

# REAL-MONEY ORDER EXECUTION IS DISABLED.

The application is initially:

# ANALYSIS + DECISION SUPPORT + SIGNALS ONLY.

The user will manually place any real trade through Midas or another broker outside this application.

The system may provide:

* market analysis
* LONG scenarios
* SHORT scenarios
* WAIT decisions
* NO TRADE decisions
* setup states
* setup-quality scores
* entry zones
* invalidation levels
* stop-loss calculations
* take-profit scenarios
* position-size calculations
* account-risk calculations
* margin analysis
* live monitoring
* alerts
* paper trading
* market replay
* backtesting
* shadow mode
* educational explanations.

The system must NOT:

* send real broker orders
* execute real trades
* automatically buy
* automatically sell
* automatically open LONG positions
* automatically open SHORT positions
* automatically close real positions
* modify real stops
* modify real take-profit orders
* scrape Midas
* browser-automate Midas
* request Midas credentials
* store broker passwords
* bypass broker confirmation systems.

Real broker execution is NOT part of the current implementation scope.

Architectural preparation for future execution is allowed ONLY as an abstraction.

For example, future architecture MAY contain a conceptual:

BrokerExecutionProvider

or:

OrderExecutionPort

but:

* no real adapter should be implemented now
* no real credentials should be requested
* no live orders should be sent
* the current application must function fully without a broker integration.

Before any future real-money execution capability is considered, the project must first successfully complete and evaluate:

1. deterministic analysis
2. risk engine
3. paper trading
4. market replay
5. realistic backtesting
6. walk-forward validation
7. shadow mode
8. live-data validation
9. auditability
10. system reliability assessment.

The preferred future progression, if ever approved, is:

ANALYSIS ONLY
→
PAPER TRADING
→
REPLAY
→
BACKTEST
→
SHADOW MODE
→
OPTIONAL HUMAN-CONFIRMED SEMI-AUTOMATIC EXECUTION
→
only after a separate explicit decision and safety review.

Fully autonomous real-money execution must NEVER be assumed to be a project requirement.

Any future execution integration must require:

* an official supported broker API
* explicit user authorization
* deterministic risk checks
* order-size validation
* idempotency
* duplicate-order protection
* stale-data protection
* connection-failure handling
* partial-fill handling
* reconciliation
* audit logs
* explicit kill switch
* separate implementation approval.

Until such a future phase is explicitly authorized:

# THE USER MAKES ALL REAL TRADING DECISIONS AND MANUALLY ENTERS ALL REAL ORDERS.





