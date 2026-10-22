"""Glue between the database and the portfolio engine."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import Instrument, Price, Transaction
from app.services import portfolio as pf


def load_transactions(db: Session, exclude_id: int | None = None) -> list[pf.Tx]:
    q = select(Transaction).options(joinedload(Transaction.instrument)).order_by(Transaction.id)
    if exclude_id is not None:
        q = q.where(Transaction.id != exclude_id)
    rows = db.scalars(q).all()
    return [
        pf.Tx(
            date=r.date, type=r.type.value, symbol=r.instrument.symbol if r.instrument else None,
            quantity=float(r.quantity or 0), price=float(r.price or 0),
            amount=float(r.amount or 0), fees=float(r.fees or 0),
        )
        for r in rows
    ]


def load_prices(db: Session) -> pd.DataFrame:
    rows = db.execute(
        select(Price.date, Instrument.symbol, Price.close).join(Instrument)
    ).all()
    df = pd.DataFrame(rows, columns=["date", "symbol", "close"])
    if not df.empty:
        df["close"] = df["close"].astype(float)
    return df


class PortfolioView:
    """Everything the API needs, computed once per request."""

    def __init__(self, db: Session):
        self.settings = get_settings()
        self.instruments = {i.symbol: i for i in db.scalars(select(Instrument)).all()}
        self.txs = load_transactions(db)
        self.prices = load_prices(db)
        self.book = pf.build_book(self.txs)
        self.series = pf.valuation_series(self.txs, self.prices) if not self.prices.empty else pd.DataFrame()

    def latest_prices(self) -> dict[str, float]:
        if self.prices.empty:
            return {}
        last = self.prices.sort_values("date").groupby("symbol").tail(1)
        return dict(zip(last.symbol, last.close, strict=True))

    def holdings(self) -> list[dict]:
        prices = self.latest_prices()
        rows = []
        for symbol in sorted(self.book.lots):
            qty = self.book.quantity(symbol)
            if qty <= 1e-9:
                continue
            cost = self.book.cost_basis(symbol)
            price = prices.get(symbol, 0.0)
            mv = qty * price
            inst = self.instruments.get(symbol)
            rows.append(dict(
                symbol=symbol, name=inst.name if inst else symbol,
                asset_class=inst.asset_class.value if inst else "Unknown",
                quantity=qty, avg_cost=cost / qty, cost_basis=cost, price=price, market_value=mv,
                unrealised_pnl=mv - cost, unrealised_pct=(mv - cost) / cost if cost else 0.0,
            ))
        total = sum(r["market_value"] for r in rows) + self.book.cash
        for r in rows:
            r["weight"] = r["market_value"] / total if total else 0.0
        return rows

    def summary(self) -> dict:
        holdings = self.holdings()
        mv = sum(h["market_value"] for h in holdings)
        total = mv + self.book.cash
        perf = pf.performance(self.series, self.book.external_flows, self.settings.risk_free_rate)
        money = {k: round(v, 2) for k, v in dict(
            market_value=mv, cash=self.book.cash, total_value=total,
            net_contributions=self.book.contributions, total_gain=total - self.book.contributions,
            realised_pnl=self.book.realised_pnl, unrealised_pnl=sum(h["unrealised_pnl"] for h in holdings),
            dividends=self.book.dividends, fees=self.book.fees,
        ).items()}
        ratios = {k: (round(v, 6) if v is not None else None) for k, v in perf.items()}
        return dict(
            as_of=self.series.index[-1].date() if not self.series.empty else None,
            currency=self.settings.base_currency, **money, **ratios,
        )

    def allocation(self) -> list[dict]:
        buckets: dict[str, float] = {}
        for h in self.holdings():
            buckets[h["asset_class"]] = buckets.get(h["asset_class"], 0) + h["market_value"]
        if self.book.cash > 0.005:
            buckets["Cash"] = buckets.get("Cash", 0) + self.book.cash
        total = sum(buckets.values()) or 1
        return [{"asset_class": k, "value": v, "weight": v / total}
                for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])]

    def timeseries(self, freq: str = "D") -> list[dict]:
        if self.series.empty:
            return []
        s = self.series
        if freq == "W":
            s = s.resample("W-FRI").last()
        elif freq == "M":
            s = s.resample("ME").last()
        return [
            dict(date=d.date(), total_value=round(r.total_value, 2), contributions=round(r.contributions, 2),
                 cash=round(r.cash, 2), twr_index=round(r.twr_index, 6), drawdown=round(r.drawdown, 6))
            for d, r in s.iterrows()
        ]
