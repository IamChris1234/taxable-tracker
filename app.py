from __future__ import annotations

from datetime import date
from typing import Optional, List, Dict, Tuple
import os
import secrets
import io
import csv

from fastapi import (
    FastAPI,
    Request,
    Form,
    Depends,
    HTTPException,
    status,
    APIRouter,
)
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from sqlmodel import Field, SQLModel, Session, create_engine, select


# =========================================================
# App config
# =========================================================
ENABLE_DOCS = os.getenv("ENABLE_DOCS", "0").lower() in ("1", "true", "yes")

app = FastAPI(
    title="Taxable Tracker",
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
)

templates = Jinja2Templates(directory="templates")


# =========================================================
# Auth (HTTP Basic)
# =========================================================
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


# Protect all UI routes
ui = APIRouter(prefix="/ui", dependencies=[Depends(require_login)])


# =========================================================
# Database
# =========================================================
db_url = os.getenv("DATABASE_URL", "sqlite:///tracker.db")

# Render/Railway fix: postgres:// -> postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, echo=False)


# =========================================================
# Models
# =========================================================
class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)


class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tx_date: date
    type: str            # "income" or "expense"
    source: str = "rental"  # "rental" or "work"
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


DEFAULT_CATEGORIES = [
    "Rental Income",
    "Repairs & Maintenance",
    "Utilities",
    "Condo Fees",
    "Insurance",
    "Property Tax",
    "Office Supplies",
    "Meals",
    "Parking",
    "Phone/Internet",
    "Tools",
    "Fuel",
]


# =========================================================
# Startup
# =========================================================
@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

    # seed categories if empty
    with Session(engine) as session:
        any_cat = session.exec(select(Category)).first()
        if not any_cat:
            for c in DEFAULT_CATEGORIES:
                session.add(Category(name=c))
            session.commit()


# =========================================================
# Helpers
# =========================================================
def ytd_range(year: int) -> Tuple[date, date]:
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)
    return start, end


def get_categories(session: Session) -> List[Category]:
    return session.exec(select(Category).order_by(Category.name.asc())).all()


def compute_fuel_stats(rows: List[FuelLog]) -> List[Dict]:
    """
    Returns newest first; includes km_since_last and cost_per_km when possible.
    """
    rows_sorted = sorted(rows, key=lambda r: r.fill_date)
    out = []
    prev = None
    for f in rows_sorted:
        entry = {
            "id": f.id,
            "fill_date": f.fill_date,
            "odometer_km": f.odometer_km,
            "total_cost": f.total_cost,
            "notes": f.notes,
            "km_since_last": None,
            "cost_per_km": None,
        }
        if prev and f.odometer_km > prev.odometer_km:
            km = f.odometer_km - prev.odometer_km
            entry["km_since_last"] = km
            entry["cost_per_km"] = round(f.total_cost / km, 3) if km else None
        out.append(entry)
        prev = f

    return list(reversed(out))


# =========================================================
# Public root
# =========================================================
@app.get("/")
def root():
    return {"status": "ok", "ui": "/ui"}


# =========================================================
# UI: Home
# =========================================================
@ui.get("", response_class=HTMLResponse)
def ui_home(request: Request):
    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).order_by(Transaction.tx_date.desc())
        ).all()[:15]

        fuels = session.exec(
            select(FuelLog).order_by(FuelLog.fill_date.desc())
        ).all()[:30]

    fuel_view = compute_fuel_stats(list(reversed(fuels)))[:10]

    return templates.TemplateResponse(
        "home.html",
        {"request": request, "transactions": txs, "fuel": fuel_view},
    )


# =========================================================
# UI: Transaction (GET)
# =========================================================
@ui.get("/transaction/new", response_class=HTMLResponse)
def ui_new_transaction(
    request: Request,
    type: str = "expense",
    source: str = "rental",
):
    with Session(engine) as session:
        categories = get_categories(session)

    return templates.TemplateResponse(
        "new_transaction.html",
        {
            "request": request,
            "default_type": type,
            "default_source": source,
            "categories": categories,
        },
    )


# =========================================================
# UI: Transaction (POST)  ✅ SAVES
# =========================================================
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
    # basic validation
    if type not in ("income", "expense"):
        raise HTTPException(status_code=400, detail="type must be income or expense")
    if source not in ("rental", "work"):
        raise HTTPException(status_code=400, detail="source must be rental or work")

    tx = Transaction(
        tx_date=date.fromisoformat(tx_date),
        source=source,
        type=type,
        category=category,
        amount=float(amount),
        vendor=vendor.strip() or None,
        notes=notes.strip() or None,
    )

    with Session(engine) as session:
        session.add(tx)
        session.commit()

    return RedirectResponse(url="/ui", status_code=303)


# =========================================================
# UI: Add Category ✅
# =========================================================
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


# =========================================================
# UI: Fuel
# =========================================================
@ui.get("/fuel")
def ui_fuel_redirect():
    # lets /ui/fuel work (redirect to form)
    return RedirectResponse(url="/ui/fuel/new", status_code=303)


@ui.get("/fuel/new", response_class=HTMLResponse)
def ui_new_fuel(request: Request):
    return templates.TemplateResponse("new_fuel.html", {"request": request})


# ✅ Fuel save
@ui.post("/fuel/new")
def ui_create_fuel(
    fill_date: str = Form(...),
    odometer_km: int = Form(...),
    total_cost: float = Form(...),
    notes: str = Form(""),
):
    fill = FuelLog(
        fill_date=date.fromisoformat(fill_date),
        odometer_km=int(odometer_km),
        total_cost=float(total_cost),
        notes=notes.strip() or None,
    )

    with Session(engine) as session:
        session.add(fill)
        session.commit()

    return RedirectResponse(url="/ui", status_code=303)


# =========================================================
# UI: Reports
# =========================================================
@ui.get("/report", response_class=HTMLResponse)
def ui_report(
    request: Request,
    year: int = date.today().year,
    include_fuel_log: int = 1,  # 1 = add FuelLog totals into report expenses
):
    start, end = ytd_range(year)

    with Session(engine) as session:
        txs = session.exec(
            select(Transaction)
            .where(Transaction.tx_date >= start, Transaction.tx_date < end)
            .order_by(Transaction.tx_date.desc())
        ).all()

        fuels = session.exec(
            select(FuelLog)
            .where(FuelLog.fill_date >= start, FuelLog.fill_date < end)
            .order_by(FuelLog.fill_date.desc())
        ).all()

    income_total = sum(t.amount for t in txs if t.type == "income")
    expense_total = sum(t.amount for t in txs if t.type == "expense")

    by_category_expense: Dict[str, float] = {}
    for t in txs:
        if t.type == "expense":
            by_category_expense[t.category] = by_category_expense.get(t.category, 0.0) + t.amount

    fuel_total = sum(f.total_cost for f in fuels)
    if include_fuel_log:
        by_category_expense["Fuel"] = by_category_expense.get("Fuel", 0.0) + fuel_total
        expense_total += fuel_total

    by_category = sorted(
        ((k, round(v, 2)) for k, v in by_category_expense.items()),
        key=lambda x: x[0],
    )

    fuel_rows = compute_fuel_stats(list(reversed(fuels)))

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "year": year,
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "net_total": round(income_total - expense_total, 2),
            "by_category": by_category,      # used by pie chart
            "transactions": txs,             # full list
            "fuel_rows": fuel_rows,          # shown on report page
            "include_fuel_log": include_fuel_log,
        },
    )


# =========================================================
# UI: CSV export backup
# =========================================================
@ui.get("/export.csv")
def export_csv(year: int = date.today().year):
    start, end = ytd_range(year)

    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).where(Transaction.tx_date >= start, Transaction.tx_date < end)
        ).all()

        fuels = session.exec(
            select(FuelLog).where(FuelLog.fill_date >= start, FuelLog.fill_date < end)
        ).all()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["SECTION", "date", "type", "source", "category", "amount", "vendor", "notes"])
    for t in txs:
        writer.writerow([
            "TRANSACTION",
            t.tx_date.isoformat(),
            t.type,
            t.source,
            t.category,
            f"{t.amount:.2f}",
            t.vendor or "",
            t.notes or "",
        ])

    writer.writerow([])
    writer.writerow(["SECTION", "date", "odometer_km", "total_cost", "notes"])
    for f in fuels:
        writer.writerow([
            "FUEL",
            f.fill_date.isoformat(),
            f.odometer_km,
            f"{f.total_cost:.2f}",
            f.notes or "",
        ])

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="taxable_backup_{year}.csv"'},
    )


# =========================================================
# UI: Receipts (stub page for now)
# =========================================================
@ui.get("/receipts", response_class=HTMLResponse)
def ui_receipts(request: Request):
    return templates.TemplateResponse("receipts.html", {"request": request})


# Register router (must be last)
app.include_router(ui)
