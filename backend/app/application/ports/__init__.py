"""Ports: the interfaces the application layer owns and adapters implement.

Implemented in Phase 0:
    market_data.HistoricalMarketDataProvider
    ai.AIProvider
    system.ClockPort
    system.DatabaseHealthPort

Deliberately deferred, because they cannot be honestly typed until the domain
types they carry exist (master spec sections 73 and 105):
    LiveMarketDataProvider    -> Phase 13
    ContractMetadataProvider  -> Phase 3
    ScreenshotAnalyzer        -> Phase 6
    NewsProvider              -> Phase 15
    OrderExecutionPort        -> not scheduled; execution is disabled (section 120)
"""
