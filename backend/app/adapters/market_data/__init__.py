"""Historical market data adapters (master spec section 73).

Each implements ``HistoricalMarketDataProvider``. None of them calculates
anything: an adapter reads or generates candles, and every judgement about
those candles is made by the domain.
"""

from app.adapters.market_data.csv_provider import (
    CsvHistoricalMarketDataProvider,
    CsvSchemaError,
)
from app.adapters.market_data.synthetic_provider import (
    SyntheticDefect,
    SyntheticHistoricalMarketDataProvider,
    SyntheticMarketProfile,
)

__all__ = [
    "CsvHistoricalMarketDataProvider",
    "CsvSchemaError",
    "SyntheticDefect",
    "SyntheticHistoricalMarketDataProvider",
    "SyntheticMarketProfile",
]
