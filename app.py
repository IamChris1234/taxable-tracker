from datetime import date
from typing import Optional

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Field, SQLModel, Session, create_engine, select


app = FastAPI(title="Taxable Tracker (Single User)")

import os

db_url = os.getenv("DATABASE_URL", "sqlite:///tracker.db")

# Render / Railway sometimes use postgres:// but SQLAlchemy wants postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, echo=False)
templates = Jinja2Templates(directory="templates")


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tx_date: date
    type: str  # "income" or "expense"
    category: str
    amount: float
    vendor: Optional[str] = None
    notes: Optional[str] = None


class FuelLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    fill_date: date
    odometer_km: int
    total_cost: float
    notes: Optional[str] = None



@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)


@app.get("/")
def home():
    return {"status": "ok", "docs": "/docs"}


@app.get("/transactions")
def list_transactions():
    with Session(engine) as session:
        return session.exec(
            select(Transaction).order_by(Transaction.tx_date.desc())
        ).all()


@app.post("/transactions")
def add_transaction(tx: Transaction):
    if tx.type not in ("income", "expense"):
        return {"error": "type must be 'income' or 'expense'"}
    with Session(engine) as session:
        session.add(tx)
        session.commit()
        session.refresh(tx)
        return tx


@app.get("/fuel")
def list_fuel():
    with Session(engine) as session:
        rows = session.exec(select(FuelLog).order_by(FuelLog.fill_date.asc())).all()

    out = []
    prev = None
    for f in rows:
        entry = f.model_dump()
        if prev and f.odometer_km > prev.odometer_km:
            km = f.odometer_km - prev.odometer_km
            entry["km_since_last"] = km
            entry["cost_per_km"] = round(f.total_cost / km, 3)
        else:
            entry["km_since_last"] = None
            entry["cost_per_km"] = None
        out.append(entry)
        prev = f

    return list(reversed(out))


    return list(reversed(out))


@app.post("/fuel")
def add_fuel(fill: FuelLog):
    with Session(engine) as session:
        session.add(fill)
        session.commit()
        session.refresh(fill)
        return fill


@app.get("/reports/ytd")
def ytd_report(year: int):
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).where(
                Transaction.tx_date >= start,
                Transaction.tx_date < end,
            )
        ).all()

    income_total = sum(t.amount for t in txs if t.type == "income")
    expense_total = sum(t.amount for t in txs if t.type == "expense")

    by_category = {}
    for t in txs:
        by_category[t.category] = by_category.get(t.category, 0.0) + t.amount

    return {
        "year": year,
        "income_total": round(income_total, 2),
        "expense_total": round(expense_total, 2),
        "net": round(income_total - expense_total, 2),
        "by_category": {k: round(v, 2) for k, v in sorted(by_category.items())},
    }
@app.get("/ui", response_class=HTMLResponse)
def ui_home(request: Request):
    with Session(engine) as session:
        txs = session.exec(select(Transaction).order_by(Transaction.tx_date.desc())).all()[:10]
        fuels = session.exec(select(FuelLog).order_by(FuelLog.fill_date.desc())).all()[:10]
    return templates.TemplateResponse("home.html", {"request": request, "transactions": txs, "fuel": fuels})


@app.get("/ui/transaction/new", response_class=HTMLResponse)
def ui_new_transaction(
    request: Request,
    type: str = "expense",
    category: str = "",
):
    return templates.TemplateResponse(
        "new_transaction.html",
        {
            "request": request,
            "default_type": type,
            "default_category": category,
        },
    )


@app.post("/ui/transaction/new")
def ui_create_transaction(
    tx_date: str = Form(...),
    type: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    tx = Transaction(
        tx_date=date.fromisoformat(tx_date),
        type=type,
        category=category,
        amount=amount,
        vendor=vendor or None,
        notes=notes or None,
    )
    with Session(engine) as session:
        session.add(tx)
        session.commit()
    return RedirectResponse(url="/ui", status_code=303)


@app.get("/ui/fuel/new", response_class=HTMLResponse)
def ui_new_fuel(request: Request):
    return templates.TemplateResponse("new_fuel.html", {"request": request})


@app.post("/ui/fuel/new")
def ui_create_fuel(
    fill_date: str = Form(...),
    odometer_km: int = Form(...),
    total_cost: float = Form(...),
    notes: str = Form(""),
):
    fill = FuelLog(
        fill_date=date.fromisoformat(fill_date),
        odometer_km=odometer_km,
        total_cost=total_cost,
        notes=notes or None,
    )
    with Session(engine) as session:
        session.add(fill)
        session.commit()
    return RedirectResponse(url="/ui", status_code=303)

