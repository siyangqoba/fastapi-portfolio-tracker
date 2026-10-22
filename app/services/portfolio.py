"""Portfolio maths: FIFO lots, cash, valuation series and performance metrics.

Everything here works on plain data (lists of transactions and a price table),
so it can be unit-tested without a database.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

TRADING_DAYS = 252
# Within one day: cash in (deposits, dividends, sale proceeds) before cash out (fees, buys, withdrawals).
TYPE_ORDER = {"DEPOSIT": 0, "DIVIDEND": 1, "SELL": 2, "FEE": 3, "BUY": 4, "WITHDRAWAL": 5}


class PortfolioError(ValueError):
    pass


@dataclass
class Tx:
    date: date
    type: str
    symbol: str | None = None
    quantity: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    fees: float = 0.0


@dataclass
class Lot:
    quantity: float
    unit_cost: float  # includes buy fees


@dataclass
class Book:
    lots: dict[str, deque[Lot]] = field(default_factory=lambda: defaultdict(deque))
    cash: float = 0.0
    contributions: float = 0.0
    realised_pnl: float = 0.0
    dividends: float = 0.0
    fees: float = 0.0
    external_flows: list[tuple[date, float]] = field(default_factory=list)

    def quantity(self, symbol: str) -> float:
        return sum(lot.quantity for lot in self.lots[symbol])

    def cost_basis(self, symbol: str) -> float:
        return sum(lot.quantity * lot.unit_cost for lot in self.lots[symbol])


def sort_transactions(txs: list[Tx]) -> list[Tx]:
    return sorted(txs, key=lambda t: (t.date, TYPE_ORDER[t.type]))


def build_book(txs: list[Tx], allow_negative_cash: bool = False) -> Book:
    """Replay transactions into FIFO lots and cash. Raises PortfolioError on impossible trades."""
    book = Book()
    for t in sort_transactions(txs):
        if t.type == "DEPOSIT":
            book.cash += t.amount
            book.contributions += t.amount
            book.external_flows.append((t.date, t.amount))
        elif t.type == "WITHDRAWAL":
            book.cash -= t.amount
            book.contributions -= t.amount
            book.external_flows.append((t.date, -t.amount))
        elif t.type == "BUY":
            gross = t.quantity * t.price + t.fees
            book.cash -= gross
            book.fees += t.fees
            book.lots[t.symbol].append(Lot(t.quantity, gross / t.quantity))
        elif t.type == "SELL":
            held = book.quantity(t.symbol)
            if t.quantity > held + 1e-9:
                raise PortfolioError(
                    f"{t.date}: cannot sell {t.quantity:g} {t.symbol}, only {held:g} held"
                )
            proceeds = t.quantity * t.price - t.fees
            book.cash += proceeds
            book.fees += t.fees
            remaining, cost = t.quantity, 0.0
            lots = book.lots[t.symbol]
            while remaining > 1e-9:
                lot = lots[0]
                take = min(lot.quantity, remaining)
                cost += take * lot.unit_cost
                lot.quantity -= take
                remaining -= take
                if lot.quantity <= 1e-9:
                    lots.popleft()
            book.realised_pnl += proceeds - cost
        elif t.type == "DIVIDEND":
            book.cash += t.amount
            book.dividends += t.amount
        elif t.type == "FEE":
            book.cash -= t.amount
            book.fees += t.amount
        if book.cash < -0.005 and not allow_negative_cash:
            raise PortfolioError(f"{t.date}: cash balance would go negative ({book.cash:,.2f})")
    return book


# --------------------------------------------------------------------------- valuation
def valuation_series(txs: list[Tx], prices: pd.DataFrame) -> pd.DataFrame:
    """Daily portfolio value.

    ``prices``: columns date, symbol, close. Returns a frame indexed by date with
    cash, market_value, total_value, flow (external, signed), contributions,
    daily_return (time-weighted), twr_index and drawdown.
    """
    if not txs:
        return pd.DataFrame()
    txs = sort_transactions(txs)
    start = txs[0].date
    px = prices.pivot_table(index="date", columns="symbol", values="close").sort_index()
    px.index = pd.to_datetime(px.index)
    dates = px.index[px.index >= pd.Timestamp(start)]
    if len(dates) == 0:
        return pd.DataFrame()
    px = px.ffill().reindex(dates)

    def to_valuation_day(d: date) -> pd.Timestamp:
        # transactions on weekends/holidays count from the next priced day
        pos = dates.searchsorted(pd.Timestamp(d))
        return dates[min(pos, len(dates) - 1)]

    cash_delta: dict[pd.Timestamp, float] = defaultdict(float)
    flow: dict[pd.Timestamp, float] = defaultdict(float)
    qty_delta: dict[tuple[pd.Timestamp, str], float] = defaultdict(float)
    for t in txs:
        d = to_valuation_day(t.date)
        if t.type == "DEPOSIT":
            cash_delta[d] += t.amount
            flow[d] += t.amount
        elif t.type == "WITHDRAWAL":
            cash_delta[d] -= t.amount
            flow[d] -= t.amount
        elif t.type == "BUY":
            cash_delta[d] -= t.quantity * t.price + t.fees
            qty_delta[(d, t.symbol)] += t.quantity
        elif t.type == "SELL":
            cash_delta[d] += t.quantity * t.price - t.fees
            qty_delta[(d, t.symbol)] -= t.quantity
        elif t.type == "DIVIDEND":
            cash_delta[d] += t.amount
        elif t.type == "FEE":
            cash_delta[d] -= t.amount

    out = pd.DataFrame(index=dates)
    out["cash"] = pd.Series(cash_delta, dtype=float).reindex(dates, fill_value=0).cumsum()
    out["flow"] = pd.Series(flow, dtype=float).reindex(dates, fill_value=0)
    out["contributions"] = out["flow"].cumsum()

    symbols = sorted({s for _d, s in qty_delta})
    missing = [s for s in symbols if s not in px.columns]
    if missing:
        raise PortfolioError(f"no prices for {', '.join(missing)}")
    qty = pd.DataFrame(0.0, index=dates, columns=symbols)
    for (d, s), q in qty_delta.items():
        qty.loc[d, s] += q
    qty = qty.cumsum()
    out["market_value"] = (qty * px[symbols].fillna(0)).sum(axis=1)
    out["total_value"] = out["cash"] + out["market_value"]

    # Time-weighted return. Trades settle at the day's close, so a deposit made today has not
    # earned anything yet: strip it out of today's value (end-of-day flow convention).
    prev = out["total_value"].shift(1).fillna(0)
    out["daily_return"] = np.where(
        prev > 0, (out["total_value"] - out["flow"]) / prev.where(prev > 0) - 1, 0.0
    )
    out["twr_index"] = (1 + out["daily_return"]).cumprod()
    out["drawdown"] = out["twr_index"] / out["twr_index"].cummax() - 1
    out.index.name = "date"
    return out


# --------------------------------------------------------------------------- metrics
def xirr(flows: list[tuple[date, float]], tol: float = 1e-7) -> float | None:
    """Money-weighted annual return. Flows are from the investor's side of the table:
    money put in is negative, money (or value) received is positive."""
    if len(flows) < 2 or not any(f > 0 for _d, f in flows) or not any(f < 0 for _d, f in flows):
        return None
    t0 = min(d for d, _f in flows)
    years = [((d - t0).days / 365.0, f) for d, f in flows]

    def npv(rate: float) -> float:
        return sum(f / (1 + rate) ** y for y, f in years)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def performance(series: pd.DataFrame, flows: list[tuple[date, float]], risk_free: float) -> dict:
    empty = dict(twr=None, twr_annualised=None, xirr=None, volatility=None, sharpe=None, max_drawdown=None)
    if series.empty or len(series) < 2:
        return empty
    rets = series["daily_return"].iloc[1:]
    twr = float(series["twr_index"].iloc[-1] - 1)
    days = (series.index[-1] - series.index[0]).days or 1
    twr_ann = (1 + twr) ** (365 / days) - 1 if days >= 365 else None
    vol = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS)) if len(rets) > 20 else None
    ann_for_sharpe = twr_ann if twr_ann is not None else (1 + twr) ** (365 / days) - 1
    sharpe = (ann_for_sharpe - risk_free) / vol if vol else None
    end_value = float(series["total_value"].iloc[-1])
    investor_flows = [(d, -f) for d, f in flows] + [(series.index[-1].date(), end_value)]
    return dict(
        twr=twr,
        twr_annualised=twr_ann,
        xirr=xirr(investor_flows),
        volatility=vol,
        sharpe=sharpe,
        max_drawdown=float(series["drawdown"].min()),
    )


def monthly_returns(series: pd.DataFrame) -> list[dict]:
    """Compounded time-weighted return per calendar month."""
    if series.empty:
        return []
    monthly = (1 + series["daily_return"]).groupby(series.index.to_period("M")).prod() - 1
    return [{"year": p.year, "month": p.month, "return": float(r)} for p, r in monthly.items()]
