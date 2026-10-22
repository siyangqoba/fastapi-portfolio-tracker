import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AssetClass(enum.StrEnum):
    EQUITY = "Equity"
    BOND = "Bond"
    PROPERTY = "Property"
    COMMODITY = "Commodity"
    CASH = "Cash"


class TxType(enum.StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    FEE = "FEE"


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    asset_class: Mapped[AssetClass] = mapped_column(Enum(AssetClass, native_enum=False))

    prices: Mapped[list["Price"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("instrument_id", "date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))

    instrument: Mapped[Instrument] = relationship(back_populates="prices")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    type: Mapped[TxType] = mapped_column(Enum(TxType, native_enum=False))
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)  # cash amount for deposits/dividends/fees
    fees: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    instrument: Mapped[Instrument | None] = relationship()
