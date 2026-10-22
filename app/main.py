import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import Transaction
from app.routers import api
from app.services import importer

log = logging.getLogger("portfolio")
HERE = Path(__file__).resolve().parent
SAMPLE_DIR = HERE.parent / "sample_data"


def load_sample_data() -> None:
    with SessionLocal() as db:
        if db.scalar(select(func.count(Transaction.id))):
            return
        for name, fn in (("instruments", importer.import_instruments),
                         ("prices", importer.import_prices),
                         ("transactions", importer.import_transactions)):
            imported, errors = fn(db, (SAMPLE_DIR / f"{name}.csv").read_text())
            if errors:
                raise RuntimeError(f"sample {name} failed: {errors[:3]}")
            log.info("sample data: %s %s rows", name, imported)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    if get_settings().load_sample_data:
        load_sample_data()
    yield


app = FastAPI(
    title="Portfolio Tracker",
    description="Track an investment portfolio: FIFO cost basis, P&L, time- and money-weighted returns, risk.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(api.router)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"currency": get_settings().base_currency})


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
