"""On-demand analysis of user-supplied historical market data (Phase 8).

This package is the smallest **real** runtime path from an OHLCV upload to a
finished deterministic analysis. It orchestrates the Phase 1-7 engines; it
computes nothing itself.

Deliberately inert at import time so the import-linter contracts can name it
without the package pulling in half the system.
"""
