# =========================
# Imports
# =========================
from datetime import date
from typing import Optional
import os
import secrets

from fastapi import (
    FastAPI, Request, Form, Depends, HTTPException, status, APIRouter
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from sqlmodel import Field, SQLModel, Session, create_engine, select


# =========================
# App + Auth
# =========================
app = FastAPI(title="Taxable Tracker (Single User)")
templates = Jinja2Templates(directory="templates")

security = HTTPBasic()

def require_login(credentials: HTTPBasicCredentials = Depends(security)):
    user = os.getenv("APP_USER", "")
    pw = os.getenv("APP_PASS", "")

    ok_user = secrets.compare_digest(credentials.username, user)
    ok_pw = secrets.compare_digest(credentials.password, pw)

    if not (ok_user and ok_pw):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return True

ui = APIRouter(prefix="/ui", dependencies=[Depends(require_login)])


# =========================
# Database
# =========================
db_url = os.getenv("DATABASE_URL", "sqlite:///tracker.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, echo=False)


# =========================
# Models
# =========================
class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tx_date: date
    type: str  # income | expense
    source: str = "work"  # work | rental  (optional but recommended)
    category: str
    amount: float
    vendor: Optional[str] = None
    notes: Optional[str] = None


# =========================
# Startup: create tables + seed categories
# =========================
DEFAULT_CATEGORIES = [
    "Rental Income",
    "Repairs & Maintenance",
    "Utilities",
    "Condo Fees",
    "Insurance",
    "Property Tax",
    "Office Supplies",
    "Fuel",
    "Meals",
    "Parking",
    "Phone/Internet",
    "Tools",
]

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

    # seed categories only if empty
    with Session(engine) as session:
        existing = session.exec(select(Category)).first()
        if not existing:
            for name in DEFAULT_CATEGORIES:
                session.add(Category(name=name))
            session.commit()


# =========================
# Public root
# =========================
@app.get("/")
def root():
    return {"status": "ok", "ui": "/ui"}


# =========================
# UI: Home
# =========================
@ui.get("", response_class=HTMLResponse)
def ui_home(request: Request):
    with Session(engine) as session:
        txs = session.exec(select(Transaction).order_by(Transaction.tx_date.desc())).all()[:15]

    return templates.TemplateResponse(
        "home.html",
        {"request": request, "transactions": txs},
    )


# =========================
# UI: Add Category
# =========================
@ui.post("/category/add")
def ui_add_category(name: str = Form(...)):
    clean = name.strip()
    if not clean:
        return RedirectResponse(url="/ui/transaction/new", status_code=303)

    with Session(engine) as session:
        exists = session.exec(select(Category).where(Category.name == clean)).first()
        if not exists:
            session.add(Category(name=clean))
            session.commit()

    return RedirectResponse(url="/ui/transaction/new", status_code=303)


# =========================
# UI: New Transaction (GET)
# =========================
@ui.get("/transaction/new", response_class=HTMLResponse)
def ui_new_transaction(
    request: Request,
    type: str = "expense",
    source: str = "rental",
):
    with Session(engine) as session:
        categories = session.exec(select(Category).order_by(Category.name.asc())).all()

    return templates.TemplateResponse(
        "new_transaction.html",
        {
            "request": request,
            "default_type": type,
            "default_source": source,
            "categories": categories,
        },
    )


# =========================
# UI: New Transaction (POST)
# =========================
@ui.post("/transaction/new")
def ui_create_transaction(
    tx_date: str = Form(...),
    source: str = Form("rental"),
    type: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    tx = Transaction(
        tx_date=date.fromisoformat(tx_date),
        source=source,
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


# =========================
# UI: Reports
# =========================
@ui.get("/report", response_class=HTMLResponse)
def ui_report(request: Request, year: int = date.today().year):
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    with Session(engine) as session:
        txs = session.exec(
            select(Transaction)
            .where(Transaction.tx_date >= start, Transaction.tx_date < end)
            .order_by(Transaction.tx_date.desc())
        ).all()

    income_total = sum(t.amount for t in txs if t.type == "income")
    expense_total = sum(t.amount for t in txs if t.type == "expense")

    by_category_expense = {}
    for t in txs:
        if t.type == "expense":
            by_category_expense[t.category] = by_category_expense.get(t.category, 0.0) + t.amount

    by_category_list = sorted(((k, round(v, 2)) for k, v in by_category_expense.items()), key=lambda x: x[0])

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "year": year,
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "net_total": round(income_total - expense_total, 2),
            "by_category": by_category_list,
            "transactions": txs,  # ✅ full list in reports
        },
    )


# Register router
app.include_router(ui)
