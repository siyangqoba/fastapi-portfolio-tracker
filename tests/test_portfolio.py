from datetime import date

import pandas as pd
import pytest

from app.services.portfolio import (
    PortfolioError,
    Tx,
    build_book,
    monthly_returns,
    performance,
    valuation_series,
    xirr,
)


def d(day: int, month: int = 1, year: int = 2024) -> date:
    return date(year, month, day)


def test_fifo_realised_pnl_includes_fees():
    book = build_book([
        Tx(d(1), "DEPOSIT", amount=10_000),
        Tx(d(2), "BUY", "AAA", quantity=10, price=100, fees=10),   # unit cost 101
        Tx(d(3), "BUY", "AAA", quantity=10, price=120, fees=0),    # unit cost 120
        Tx(d(4), "SELL", "AAA", quantity=15, price=130, fees=5),
    ])
    # proceeds 15*130-5 = 1945; cost = 10*101 + 5*120 = 1610
    assert book.realised_pnl == pytest.approx(335)
    assert book.quantity("AAA") == pytest.approx(5)
    assert book.cost_basis("AAA") == pytest.approx(600)
    assert book.cash == pytest.approx(10_000 - 1010 - 1200 + 1945)
    assert book.fees == pytest.approx(15)


def test_cannot_oversell():
    with pytest.raises(PortfolioError, match="only 1 held"):
        build_book([
            Tx(d(1), "DEPOSIT", amount=1000),
            Tx(d(2), "BUY", "AAA", quantity=1, price=10),
            Tx(d(3), "SELL", "AAA", quantity=2, price=10),
        ])


def test_cannot_spend_money_you_do_not_have():
    with pytest.raises(PortfolioError, match="negative"):
        build_book([Tx(d(1), "DEPOSIT", amount=100), Tx(d(1), "BUY", "AAA", quantity=2, price=60)])


def test_same_day_cash_in_before_cash_out():
    # listed out of order on purpose: the deposit still funds the buy
    book = build_book([Tx(d(5), "BUY", "AAA", quantity=1, price=50), Tx(d(5), "DEPOSIT", amount=50)])
    assert book.cash == 0


def test_dividends_withdrawals_and_fees():
    book = build_book([
        Tx(d(1), "DEPOSIT", amount=1000),
        Tx(d(2), "DIVIDEND", "AAA", amount=40),
        Tx(d(3), "FEE", amount=15),
        Tx(d(4), "WITHDRAWAL", amount=200),
    ])
    assert book.cash == 825
    assert book.contributions == 800
    assert book.dividends == 40
    assert book.external_flows == [(d(1), 1000), (d(4), -200)]


def prices_frame(rows):
    return pd.DataFrame(rows, columns=["date", "symbol", "close"])


def test_twr_ignores_deposits():
    # Price goes 100 -> 110 -> 121: +10% a day, regardless of the big deposit on day 2.
    prices = prices_frame([(d(1), "AAA", 100), (d(2), "AAA", 110), (d(3), "AAA", 121)])
    txs = [
        Tx(d(1), "DEPOSIT", amount=1000), Tx(d(1), "BUY", "AAA", quantity=10, price=100),
        Tx(d(2), "DEPOSIT", amount=11_000), Tx(d(2), "BUY", "AAA", quantity=100, price=110),
    ]
    s = valuation_series(txs, prices)
    assert list(s.total_value) == pytest.approx([1000, 12_100, 13_310])
    assert s.daily_return.iloc[1] == pytest.approx(0.10)
    assert s.daily_return.iloc[2] == pytest.approx(0.10)
    assert s.twr_index.iloc[-1] == pytest.approx(1.21)


def test_weekend_transactions_value_on_next_trading_day():
    prices = prices_frame([(date(2024, 1, 5), "AAA", 10), (date(2024, 1, 8), "AAA", 11)])
    txs = [Tx(date(2024, 1, 6), "DEPOSIT", amount=100), Tx(date(2024, 1, 6), "BUY", "AAA", quantity=10, price=10)]
    s = valuation_series(txs, prices)
    assert s.index[0] == pd.Timestamp("2024-01-08")
    assert s.total_value.iloc[0] == 110


def test_drawdown_from_peak():
    prices = prices_frame([(d(i), "AAA", p) for i, p in enumerate([100, 120, 90, 108], start=1)])
    txs = [Tx(d(1), "DEPOSIT", amount=100), Tx(d(1), "BUY", "AAA", quantity=1, price=100)]
    s = valuation_series(txs, prices)
    assert s.drawdown.min() == pytest.approx(90 / 120 - 1)
    assert s.drawdown.iloc[-1] == pytest.approx(108 / 120 - 1)


def test_xirr_simple_cases():
    assert xirr([(date(2023, 1, 1), -1000), (date(2024, 1, 1), 1100)]) == pytest.approx(0.10, abs=1e-4)
    # two deposits: the rate must make the net present value zero
    flows = [(date(2023, 1, 1), -1000), (date(2023, 7, 1), -1000), (date(2024, 1, 1), 2200)]
    r = xirr(flows)
    npv = sum(f / (1 + r) ** ((dt - date(2023, 1, 1)).days / 365) for dt, f in flows)
    assert npv == pytest.approx(0, abs=1e-4)
    assert 0.13 < r < 0.14
    assert xirr([(date(2023, 1, 1), -1000)]) is None


def test_performance_metrics_on_constant_growth():
    days = pd.bdate_range("2023-01-02", periods=400)
    prices = prices_frame([(x.date(), "AAA", 100 * 1.0005 ** i) for i, x in enumerate(days)])
    txs = [Tx(days[0].date(), "DEPOSIT", amount=1000), Tx(days[0].date(), "BUY", "AAA", quantity=10, price=100)]
    s = valuation_series(txs, prices)
    perf = performance(s, [(days[0].date(), 1000)], risk_free=0.0)
    assert perf["max_drawdown"] == pytest.approx(0)
    assert perf["volatility"] == pytest.approx(0, abs=1e-9)
    assert perf["twr"] == pytest.approx(1.0005 ** 399 - 1)
    assert perf["xirr"] == pytest.approx(perf["twr_annualised"], abs=1e-3)


def test_monthly_returns_compound_daily():
    prices = prices_frame([(date(2024, 1, 31), "AAA", 100), (date(2024, 2, 1), "AAA", 110),
                           (date(2024, 2, 29), "AAA", 121)])
    txs = [Tx(date(2024, 1, 31), "DEPOSIT", amount=100), Tx(date(2024, 1, 31), "BUY", "AAA", quantity=1, price=100)]
    out = monthly_returns(valuation_series(txs, prices))
    assert out == [{"year": 2024, "month": 1, "return": 0.0},
                   {"year": 2024, "month": 2, "return": pytest.approx(0.21)}]
