"""CSV imports. Each import is all-or-nothing: if any row is invalid, nothing is saved."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import AssetClass, Instrument, Price, Transaction
from app.schemas import InstrumentIn, TransactionIn
from app.services import portfolio as pf
from app.services.analytics import load_transactions

TX_COLUMNS = {"date", "type", "symbol", "quantity", "price", "amount", "fees"}
PRICE_COLUMNS = {"date", "symbol", "close"}
INSTRUMENT_COLUMNS = {"symbol", "name", "asset_class"}


def _reader(content: str, required: set[str]) -> csv.DictReader:
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    headers = {h.strip().lower() for h in (reader.fieldnames or [])}
    missing = required - headers
    if missing:
        raise ValueError(f"missing column(s): {', '.join(sorted(missing))}")
    reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]
    return reader


def _clean(row: dict) -> dict:
    return {k: (v.strip() if isinstance(v, str) else v) or None for k, v in row.items() if k}


def _error_message(exc: ValidationError) -> str:
    return "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())


def import_instruments(db: Session, content: str) -> tuple[int, list[str]]:
    errors, items = [], []
    for n, row in enumerate(_reader(content, INSTRUMENT_COLUMNS), start=2):
        row = _clean(row)
        try:
            row["symbol"] = (row.get("symbol") or "").upper()
            row["asset_class"] = AssetClass((row.get("asset_class") or "").title())
            items.append(InstrumentIn(**row))
        except (ValidationError, ValueError) as exc:
            msg = _error_message(exc) if isinstance(exc, ValidationError) else str(exc)
            errors.append(f"row {n}: {msg}")
    if errors:
        return 0, errors
    existing = {i.symbol: i for i in db.scalars(select(Instrument)).all()}
    for item in items:
        if item.symbol in existing:
            existing[item.symbol].name = item.name
            existing[item.symbol].asset_class = item.asset_class
        else:
            db.add(Instrument(**item.model_dump()))
    db.commit()
    return len(items), []


def import_prices(db: Session, content: str) -> tuple[int, list[str]]:
    instruments = {i.symbol: i.id for i in db.scalars(select(Instrument)).all()}
    errors, rows = [], []
    for n, row in enumerate(_reader(content, PRICE_COLUMNS), start=2):
        row = _clean(row)
        try:
            symbol = (row.get("symbol") or "").upper()
            if symbol not in instruments:
                raise ValueError(f"unknown symbol '{symbol}'")
            close = Decimal(row["close"])
            if close <= 0:
                raise ValueError("close must be positive")
            rows.append(dict(instrument_id=instruments[symbol], date=date.fromisoformat(row["date"]), close=close))
        except (ValueError, TypeError, InvalidOperation) as exc:
            errors.append(f"row {n}: {exc or 'invalid value'}")
    if errors:
        return 0, errors
    # upsert so re-importing a price file updates existing closes
    if db.bind.dialect.name == "sqlite":
        stmt = sqlite_insert(Price)
        stmt = stmt.on_conflict_do_update(index_elements=["instrument_id", "date"], set_={"close": stmt.excluded.close})
    else:
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(Price)
        stmt = stmt.on_conflict_do_update(index_elements=["instrument_id", "date"], set_={"close": stmt.excluded.close})
    for i in range(0, len(rows), 1000):
        db.execute(stmt, rows[i:i + 1000])
    db.commit()
    return len(rows), []


def import_transactions(db: Session, content: str) -> tuple[int, list[str]]:
    instruments = {i.symbol: i.id for i in db.scalars(select(Instrument)).all()}
    errors, items = [], []
    for n, row in enumerate(_reader(content, TX_COLUMNS), start=2):
        row = _clean(row)
        try:
            if row.get("type"):
                row["type"] = row["type"].upper()
            if row.get("symbol"):
                row["symbol"] = row["symbol"].upper()
            for k in ("quantity", "price", "amount", "fees"):
                row[k] = row.get(k) or "0"
            tx = TransactionIn(**row)
            if tx.symbol and tx.symbol not in instruments:
                raise ValueError(f"unknown symbol '{tx.symbol}'")
            items.append(tx)
        except ValidationError as exc:
            errors.append(f"row {n}: {_error_message(exc)}")
        except ValueError as exc:
            errors.append(f"row {n}: {exc}")
    if errors:
        return 0, errors

    # Replay the whole history with the new rows to catch overselling / negative cash.
    candidate = load_transactions(db) + [
        pf.Tx(date=t.date, type=t.type.value, symbol=t.symbol, quantity=float(t.quantity),
              price=float(t.price), amount=float(t.amount), fees=float(t.fees))
        for t in items
    ]
    try:
        pf.build_book(candidate)
    except pf.PortfolioError as exc:
        return 0, [str(exc)]

    for t in items:
        db.add(Transaction(
            date=t.date, type=t.type, instrument_id=instruments.get(t.symbol) if t.symbol else None,
            quantity=t.quantity, price=t.price, amount=t.amount, fees=t.fees, note=t.note,
        ))
    db.commit()
    return len(items), []
