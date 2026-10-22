from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import AssetClass, TxType


class InstrumentIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9.\-]+$")
    name: str = Field(min_length=1, max_length=120)
    asset_class: AssetClass


class InstrumentOut(InstrumentIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TransactionIn(BaseModel):
    date: date
    type: TxType
    symbol: str | None = None
    quantity: Decimal = Decimal(0)
    price: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    note: str | None = None

    @model_validator(mode="after")
    def check_fields(self):
        if self.fees < 0:
            raise ValueError("fees cannot be negative")
        if self.type in (TxType.BUY, TxType.SELL):
            if not self.symbol:
                raise ValueError(f"{self.type.value} needs a symbol")
            if self.quantity <= 0 or self.price <= 0:
                raise ValueError(f"{self.type.value} needs a positive quantity and price")
        elif self.type == TxType.DIVIDEND:
            if not self.symbol or self.amount <= 0:
                raise ValueError("DIVIDEND needs a symbol and a positive amount")
        elif self.amount <= 0:
            raise ValueError(f"{self.type.value} needs a positive amount")
        return self


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    date: date
    type: TxType
    symbol: str | None
    quantity: float
    price: float
    amount: float
    fees: float
    note: str | None


class ImportResult(BaseModel):
    imported: int
    errors: list[str]


class Holding(BaseModel):
    symbol: str
    name: str
    asset_class: str
    quantity: float
    avg_cost: float
    cost_basis: float
    price: float
    market_value: float
    unrealised_pnl: float
    unrealised_pct: float
    weight: float


class Summary(BaseModel):
    as_of: date | None
    currency: str
    market_value: float
    cash: float
    total_value: float
    net_contributions: float
    total_gain: float
    realised_pnl: float
    unrealised_pnl: float
    dividends: float
    fees: float
    twr: float | None = Field(description="Time-weighted return since inception")
    twr_annualised: float | None
    xirr: float | None = Field(description="Money-weighted annual return")
    volatility: float | None
    sharpe: float | None
    max_drawdown: float | None
