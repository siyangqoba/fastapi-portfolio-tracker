from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app import schemas
from app.db import get_db
from app.models import Instrument, Transaction
from app.services import importer
from app.services import portfolio as pf
from app.services.analytics import PortfolioView, load_transactions

router = APIRouter(prefix="/api", tags=["portfolio"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def get_view(db: Session = Depends(get_db)) -> PortfolioView:
    return PortfolioView(db)


async def read_csv(file: UploadFile) -> str:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (max 5 MB)")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "file must be UTF-8 encoded CSV") from exc


def run_import(fn, db: Session, content: str) -> schemas.ImportResult:
    try:
        imported, errors = fn(db, content)
    except ValueError as exc:  # bad header row
        raise HTTPException(422, detail={"imported": 0, "errors": [str(exc)]}) from exc
    if errors:
        raise HTTPException(422, detail={"imported": 0, "errors": errors[:50]})
    return schemas.ImportResult(imported=imported, errors=[])


# ----------------------------------------------------------------------------- analytics
@router.get("/summary", response_model=schemas.Summary)
def summary(view: PortfolioView = Depends(get_view)):
    return view.summary()


@router.get("/holdings", response_model=list[schemas.Holding])
def holdings(view: PortfolioView = Depends(get_view)):
    return view.holdings()


@router.get("/allocation")
def allocation(view: PortfolioView = Depends(get_view)):
    return view.allocation()


@router.get("/timeseries")
def timeseries(freq: Literal["D", "W", "M"] = Query("W"), view: PortfolioView = Depends(get_view)):
    return view.timeseries(freq)


@router.get("/monthly-returns")
def monthly_returns(view: PortfolioView = Depends(get_view)):
    return pf.monthly_returns(view.series)


# ----------------------------------------------------------------------------- instruments
@router.get("/instruments", response_model=list[schemas.InstrumentOut])
def list_instruments(db: Session = Depends(get_db)):
    return db.scalars(select(Instrument).order_by(Instrument.symbol)).all()


@router.post("/instruments", response_model=schemas.InstrumentOut, status_code=201)
def create_instrument(payload: schemas.InstrumentIn, db: Session = Depends(get_db)):
    if db.scalar(select(Instrument).where(Instrument.symbol == payload.symbol)):
        raise HTTPException(409, f"{payload.symbol} already exists")
    inst = Instrument(**payload.model_dump())
    db.add(inst)
    db.commit()
    return inst


# ----------------------------------------------------------------------------- transactions
def _tx_out(t: Transaction) -> schemas.TransactionOut:
    return schemas.TransactionOut(
        id=t.id, date=t.date, type=t.type, symbol=t.instrument.symbol if t.instrument else None,
        quantity=float(t.quantity), price=float(t.price), amount=float(t.amount), fees=float(t.fees), note=t.note,
    )


@router.get("/transactions", response_model=list[schemas.TransactionOut])
def list_transactions(
    limit: int = Query(100, le=1000), offset: int = 0, symbol: str | None = None, db: Session = Depends(get_db)
):
    q = select(Transaction).options(joinedload(Transaction.instrument)).order_by(
        Transaction.date.desc(), Transaction.id.desc()
    )
    if symbol:
        q = q.join(Instrument).where(Instrument.symbol == symbol.upper())
    return [_tx_out(t) for t in db.scalars(q.limit(limit).offset(offset)).all()]


@router.post("/transactions", response_model=schemas.TransactionOut, status_code=201)
def create_transaction(payload: schemas.TransactionIn, db: Session = Depends(get_db)):
    inst = None
    if payload.symbol:
        inst = db.scalar(select(Instrument).where(Instrument.symbol == payload.symbol.upper()))
        if not inst:
            raise HTTPException(404, f"unknown symbol {payload.symbol}")
    candidate = load_transactions(db) + [pf.Tx(
        date=payload.date, type=payload.type.value, symbol=inst.symbol if inst else None,
        quantity=float(payload.quantity), price=float(payload.price),
        amount=float(payload.amount), fees=float(payload.fees),
    )]
    try:
        pf.build_book(candidate)
    except pf.PortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc
    tx = Transaction(**payload.model_dump(exclude={"symbol"}), instrument_id=inst.id if inst else None)
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return _tx_out(tx)


@router.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if not tx:
        raise HTTPException(404, "transaction not found")
    try:
        pf.build_book(load_transactions(db, exclude_id=tx_id))
    except pf.PortfolioError as exc:
        raise HTTPException(422, f"deleting this would break the history: {exc}") from exc
    db.execute(delete(Transaction).where(Transaction.id == tx_id))
    db.commit()


# ----------------------------------------------------------------------------- imports
@router.post("/import/instruments", response_model=schemas.ImportResult)
async def import_instruments(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return run_import(importer.import_instruments, db, await read_csv(file))


@router.post("/import/prices", response_model=schemas.ImportResult)
async def import_prices(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return run_import(importer.import_prices, db, await read_csv(file))


@router.post("/import/transactions", response_model=schemas.ImportResult)
async def import_transactions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    return run_import(importer.import_transactions, db, await read_csv(file))
