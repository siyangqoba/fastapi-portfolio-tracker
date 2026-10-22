"""Generate the demo CSVs in sample_data/ (fictional funds, simulated prices).

    python scripts/make_sample_data.py
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent.parent / "sample_data"
START, END = date(2023, 1, 2), date(2026, 9, 30)

# symbol, name, asset class, start price, annual drift, annual vol, dividend yield, target weight
FUNDS = [
    ("SAEQ", "Sample SA Top 40 Equity Fund", "Equity", 72.0, 0.11, 0.18, 0.035, 0.35),
    ("GLEQ", "Sample Global Equity Feeder Fund", "Equity", 118.0, 0.13, 0.16, 0.012, 0.30),
    ("SABND", "Sample SA Government Bond Fund", "Bond", 48.0, 0.04, 0.07, 0.095, 0.20),
    ("PROP", "Sample SA Listed Property Fund", "Property", 31.0, 0.05, 0.22, 0.075, 0.10),
    ("GOLD", "Sample Gold Tracker", "Commodity", 210.0, 0.12, 0.15, 0.0, 0.05),
]


def brokerage(value: float) -> float:
    return round(max(10.0, value * 0.0025), 2)


def main(seed: int = 4) -> None:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(START, END)
    dt = 1 / 252
    prices = {}
    for sym, _n, cls, p0, mu, sigma, *_ in FUNDS:
        shocks = rng.normal((mu - sigma**2 / 2) * dt, sigma * np.sqrt(dt), len(days))
        # a sharp risk-off week in April 2025 for the growth assets
        if cls in ("Equity", "Property"):
            crash = (days >= "2025-04-02") & (days <= "2025-04-09")
            shocks[crash] -= 0.022 if sym != "PROP" else 0.03
        prices[sym] = np.round(p0 * np.exp(np.cumsum(shocks)), 2)
    px = pd.DataFrame(prices, index=days)

    OUT.mkdir(exist_ok=True)
    with open(OUT / "instruments.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name", "asset_class"])
        for sym, name, cls, *_ in FUNDS:
            w.writerow([sym, name, cls])
    with open(OUT / "prices.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "close"])
        for d, row in px.iterrows():
            for sym in px.columns:
                w.writerow([d.date().isoformat(), sym, f"{row[sym]:.2f}"])

    txs, cash = [], 0.0
    units = {f[0]: 0 for f in FUNDS}
    targets = {f[0]: f[7] for f in FUNDS}

    def invest(d: pd.Timestamp) -> None:
        """Spend available cash, topping up the most underweight funds first."""
        nonlocal cash
        total = cash + sum(units[s] * px.at[d, s] for s in units)
        gaps = sorted(units, key=lambda s: units[s] * px.at[d, s] / total - targets[s])
        for s in gaps:
            want = targets[s] * total - units[s] * px.at[d, s]
            budget = min(want, cash - 15)
            qty = int(budget // (px.at[d, s] * 1.0025))
            if qty <= 0:
                continue
            fee = brokerage(qty * px.at[d, s])
            cash -= qty * px.at[d, s] + fee
            units[s] += qty
            txs.append([d.date(), "BUY", s, qty, f"{px.at[d, s]:.2f}", "", f"{fee:.2f}", "monthly investment"])

    month_starts = days.to_series().groupby(days.to_period("M")).first()
    for i, d in enumerate(month_starts):
        amount = 50000 if i == 0 else (5000 if d.year == 2023 else 6000 if d.year == 2024 else 7000)
        cash += amount
        note = "initial lump sum" if i == 0 else "debit order"
        txs.append([d.date(), "DEPOSIT", "", "", "", f"{amount:.2f}", "", note])
        if d.month == 1 and i > 0:
            total = cash + sum(units[s] * px.at[d, s] for s in units)
            fee = round(total * 0.004, 2)
            cash -= fee
            txs.append([d.date(), "FEE", "", "", "", f"{fee:.2f}", "", "annual platform fee"])
        invest(d)
        if d.month in (3, 6, 9, 12):
            for sym, _n, _c, _p, _m, _s, yld, _t in FUNDS:
                div = round(units[sym] * px.at[d, sym] * yld / 4, 2)
                if div > 0:
                    cash += div
                    txs.append([d.date(), "DIVIDEND", sym, "", "", f"{div:.2f}", "", "quarterly distribution"])
        if d.year == 2025 and d.month == 7:
            # take profit on gold after a strong run and rebalance
            sell = units["GOLD"] // 2
            fee = brokerage(sell * px.at[d, "GOLD"])
            cash += sell * px.at[d, "GOLD"] - fee
            units["GOLD"] -= sell
            txs.append([d.date(), "SELL", "GOLD", sell, f"{px.at[d, 'GOLD']:.2f}", "", f"{fee:.2f}", "rebalance"])
            invest(d)

    with open(OUT / "transactions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "type", "symbol", "quantity", "price", "amount", "fees", "note"])
        w.writerows(txs)
    print(f"{len(px)} trading days, {len(txs)} transactions, ending cash {cash:,.2f}")


if __name__ == "__main__":
    main()
