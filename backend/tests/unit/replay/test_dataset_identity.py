"""A dataset is identified by the market it contains, and nothing else.

Identity is what makes a replay reproducible: if two sessions name the same
dataset id, they walked through the same candles. So the id must move when a
price moves, and must *not* move when something that is not the market moves -
an upload time, a file name, the order the timeframes were supplied in, or the
number of trailing zeros a person happened to type.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.replay.service import _describe
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from tests.factories_replay import FIXTURE_SYMBOL, aggregate, csv_of, five_minute

PARSER = CsvCandleTextParser()


def parsed(content: str, timeframe: Timeframe) -> tuple[Candle, ...]:
    return tuple(
        PARSER.parse(
            content, symbol=FIXTURE_SYMBOL, timeframe=timeframe, source_name="fixture.csv"
        ).candles
    )


def identity(
    content: dict[Timeframe, str], *, symbol: str = FIXTURE_SYMBOL, created: datetime | None = None
) -> str:
    candles = {timeframe: parsed(text, timeframe) for timeframe, text in content.items()}
    dataset = _describe(symbol, candles)
    assert created is None or dataset.created_at != created
    return dataset.dataset_id


ROWS = five_minute(60)
FIVE = csv_of(ROWS)
FIFTEEN = csv_of(aggregate(ROWS, Timeframe.M15))


@pytest.mark.unit
class TestTheSameMarketIsTheSameDataset:
    def test_uploading_identical_data_twice_gives_one_identity(self) -> None:
        assert identity({Timeframe.M5: FIVE}) == identity({Timeframe.M5: FIVE})

    def test_the_order_timeframes_were_supplied_in_does_not_matter(self) -> None:
        forwards = identity({Timeframe.M5: FIVE, Timeframe.M15: FIFTEEN})
        backwards = identity({Timeframe.M15: FIFTEEN, Timeframe.M5: FIVE})
        assert forwards == backwards

    def test_trailing_zeros_are_the_same_price(self) -> None:
        """``100`` and ``100.00`` are one number, and one market."""
        padded = FIVE.replace(",100,", ",100.00,").replace(",1000\n", ",1000.000\n")
        assert padded != FIVE
        assert identity({Timeframe.M5: padded}) == identity({Timeframe.M5: FIVE})

    def test_the_same_instant_in_another_offset_is_the_same_market(self) -> None:
        """09:00+00:00 and 12:00+03:00 are one moment, so one dataset."""
        header, *rows = FIVE.splitlines()
        shifted_rows = []
        for row in rows:
            stamp, *rest = row.split(",")
            local = datetime.fromisoformat(stamp).astimezone(ZoneInfo("Europe/Istanbul"))
            shifted_rows.append(",".join([local.isoformat(), *rest]))
        shifted = "\n".join([header, *shifted_rows]) + "\n"
        assert shifted != FIVE
        assert identity({Timeframe.M5: shifted}) == identity({Timeframe.M5: FIVE})

    def test_when_the_dataset_was_uploaded_is_not_part_of_it(self) -> None:
        """``created_at`` is an audit stamp; the store replaces it on write."""
        candles = {Timeframe.M5: parsed(FIVE, Timeframe.M5)}
        first = _describe(FIXTURE_SYMBOL, candles)
        second = _describe(FIXTURE_SYMBOL, candles)
        assert first.dataset_id == second.dataset_id
        assert first.created_at == second.created_at == datetime.min


@pytest.mark.unit
class TestADifferentMarketIsADifferentDataset:
    def test_one_changed_price_changes_the_identity(self) -> None:
        rows = five_minute(60)
        changed = list(rows)
        victim = changed[17]
        changed[17] = type(victim)(
            open_time=victim.open_time,
            open=victim.open,
            high=victim.high + 1,
            low=victim.low,
            close=victim.close,
            volume=victim.volume,
        )
        assert identity({Timeframe.M5: csv_of(changed)}) != identity({Timeframe.M5: FIVE})

    def test_a_changed_volume_changes_the_identity(self) -> None:
        rows = five_minute(60)
        victim = rows[3]
        rows[3] = type(victim)(
            open_time=victim.open_time,
            open=victim.open,
            high=victim.high,
            low=victim.low,
            close=victim.close,
            volume=victim.volume + 1,
        )
        assert identity({Timeframe.M5: csv_of(rows)}) != identity({Timeframe.M5: FIVE})

    def test_a_shorter_history_is_a_different_dataset(self) -> None:
        assert identity({Timeframe.M5: csv_of(ROWS[:-1])}) != identity({Timeframe.M5: FIVE})

    def test_adding_a_timeframe_changes_the_identity(self) -> None:
        one = identity({Timeframe.M5: FIVE})
        two = identity({Timeframe.M5: FIVE, Timeframe.M15: FIFTEEN})
        assert one != two

    def test_the_same_candles_under_another_symbol_are_another_dataset(self) -> None:
        """Two instruments that happened to print the same prices are not one
        market, and a session over one must not be resumed over the other."""
        assert identity({Timeframe.M5: FIVE}, symbol="OTHER_FIXTURE_FUT") != identity(
            {Timeframe.M5: FIVE}
        )


@pytest.mark.unit
def test_the_identity_is_a_content_digest_not_a_sequence() -> None:
    """Shape check: nothing in the id counts rows or increments."""
    dataset_id = identity({Timeframe.M5: FIVE})
    assert dataset_id.startswith("RD-")
    assert len(dataset_id) == 35
    assert all(character in "0123456789abcdef" for character in dataset_id[3:])
    assert datetime.now(tz=UTC).isoformat()[:4] not in dataset_id


@pytest.mark.unit
class TestTheDigestCommitsToMarketContext:
    """Identity must distinguish *markets*, not only numbers.

    The first version of this digest hashed the symbol, the timeframe labels
    and the rows into one byte stream. Concatenation hides its own boundaries:
    a crafted symbol could carry the bytes of a timeframe label and a row, and
    two different markets would then share an id. The digest is now one
    structured document, where every value is quoted and delimited.
    """

    def test_the_same_candles_under_two_symbols_are_two_datasets(self) -> None:
        first = identity({Timeframe.M5: FIVE}, symbol="AAA_FIXTURE_FUT")
        second = identity({Timeframe.M5: FIVE}, symbol="BBB_FIXTURE_FUT")
        assert first != second

    def test_the_same_candles_under_two_timeframes_are_two_datasets(self) -> None:
        """Identical numbers filed as 5M and as 15M are different markets."""
        as_five = identity({Timeframe.M5: FIVE})
        as_fifteen = _identity_raw({Timeframe.M15: FIVE})
        assert as_five != as_fifteen

    def test_moving_one_series_between_timeframes_changes_the_identity(self) -> None:
        both = identity({Timeframe.M5: FIVE, Timeframe.M15: FIFTEEN})
        swapped = _identity_raw({Timeframe.M5: FIFTEEN, Timeframe.M15: FIVE})
        assert both != swapped

    def test_no_symbol_can_absorb_a_timeframe_partition(self) -> None:
        """The concatenation collision, written out.

        A one-timeframe dataset whose symbol spells the next partition must not
        equal the two-timeframe dataset that partition describes.
        """
        rows = five_minute(3)
        one = parsed(csv_of(rows), Timeframe.M5)
        crafted = '15M[["' + rows[0].open_time.isoformat() + '"]]'
        colliding = _describe(crafted, {Timeframe.M5: one})
        honest = _describe("", {Timeframe.M5: one, Timeframe.M15: one})
        assert colliding.dataset_id != honest.dataset_id

    def test_row_counts_are_part_of_the_partition(self) -> None:
        rows = five_minute(30)
        full = _describe(FIXTURE_SYMBOL, {Timeframe.M5: parsed(csv_of(rows), Timeframe.M5)})
        short = _describe(FIXTURE_SYMBOL, {Timeframe.M5: parsed(csv_of(rows[:-1]), Timeframe.M5)})
        assert full.dataset_id != short.dataset_id


def _identity_raw(content: dict[Timeframe, str]) -> str:
    """Identity for content parsed under a *stated* timeframe.

    Separate from ``identity`` because these cases deliberately parse the same
    text under a different timeframe, which is the thing being distinguished.
    """
    candles = {
        timeframe: tuple(
            PARSER.parse(
                text, symbol=FIXTURE_SYMBOL, timeframe=timeframe, source_name="fixture.csv"
            ).candles
        )
        for timeframe, text in content.items()
    }
    return _describe(FIXTURE_SYMBOL, candles).dataset_id
