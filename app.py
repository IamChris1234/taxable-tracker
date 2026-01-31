# =========================
# Imports
# =========================
from datetime import date
from typing import Optional
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
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from sqlmodel import (
    Field,
    SQLModel,
    Session,
    create_engine,
    select,
)

# =========================
# App (disable docs)
# =========================
app = FastAPI(
    title="Taxable Tracker (Single User)",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

templates = Jinja2Templates(directory="templates")

# =========================
# Basic Auth for /ui
# =========================
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

# Render sometimes uses postgres:// but SQLAlchemy wants postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, echo=False)

# =========================
# Models
# =========================
class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tx_date: date
    type: str  # income | expense
    source: str = "rental"  # rental | work | personal
    category: str
    amount: float
    vendor: Optional[str] = None
    notes: Optional[str] = None

class FuelLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    fill_date: date
    source: str = "work"  # rental | work | personal
    odometer_km: int
    total_cost: float
    notes: Optional[str] = None

class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)

# =========================
# Startup (create tables + seed categories)
# =========================
@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

    defaults = [
        "Rental Income",
        "Condo Fees",
        "Repairs & Maintenance",
        "Utilities",
        "Insurance",
        "Property Tax",
        "Dining",
        "Fuel",
        "Car Repairs",
        "Office Supplies",
        "Phone/Internet",
        "Advertising/Marketing",
        "Bank Fees/Interest",
        "Other",
    ]
    with Session(engine) as session:
        existing = {c.name for c in session.exec(select(Category)).all()}
        for name in defaults:
            if name not in existing:
                session.add(Category(name=name))
        session.commit()

# =========================
# Public root
# =========================
@app.get("/")
def root():
    # Keep it simple: app is UI-first
    return {"status": "ok", "ui": "/ui"}

# =========================
# Helpers
# =========================
def source_label(src: str) -> str:
    return {"rental": "Rental", "work": "Work", "personal": "Personal"}.get(src, src)

# =========================
# UI: Home
# =========================
@ui.get("", response_class=HTMLResponse)
def ui_home(request: Request):
    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).order_by(Transaction.tx_date.desc())
        ).all()[:10]

        fuels = session.exec(
            select(FuelLog).order_by(FuelLog.fill_date.desc())
        ).all()[:10]

    return templates.TemplateResponse(
        "home.html",
        {"request": request, "transactions": txs, "fuel": fuels},
    )

# =========================
# UI: Add Category
# =========================
@ui.post("/category/add")
def ui_add_category(name: str = Form(...)):
    cleaned = (name or "").strip()
    if not cleaned:
        return RedirectResponse(url="/ui/transaction/new", status_code=303)

    with Session(engine) as session:
        exists = session.exec(select(Category).where(Category.name == cleaned)).first()
        if not exists:
            session.add(Category(name=cleaned))
            session.commit()

    return RedirectResponse(url="/ui/transaction/new", status_code=303)

# =========================
# UI: New Transaction
# =========================
@ui.get("/transaction/new", response_class=HTMLResponse)
def ui_new_transaction(
    request: Request,
    type: str = "expense",
    category: str = "",
    source: str = "rental",
):
    with Session(engine) as session:
        categories = session.exec(select(Category).order_by(Category.name.asc())).all()

    return templates.TemplateResponse(
        "new_transaction.html",
        {
            "request": request,
            "default_type": type,
            "default_category": category,
            "default_source": source,
            "categories": categories,
        },
    )

@ui.post("/transaction/new")
def ui_create_transaction(
    tx_date: str = Form(...),
    type: str = Form(...),
    source: str = Form("rental"),
    category: str = Form(...),
    amount: float = Form(...),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    tx = Transaction(
        tx_date=date.fromisoformat(tx_date),
        type=type,
        source=source,
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
# UI: New Fuel
# =========================
@ui.get("/fuel/new", response_class=HTMLResponse)
def ui_new_fuel(request: Request, source: str = "work"):
    return templates.TemplateResponse(
        "new_fuel.html",
        {"request": request, "default_source": source},
    )

@ui.post("/fuel/new")
def ui_create_fuel(
    fill_date: str = Form(...),
    source: str = Form("work"),
    odometer_km: int = Form(...),
    total_cost: float = Form(...),
    notes: str = Form(""),
):
    fill = FuelLog(
        fill_date=date.fromisoformat(fill_date),
        source=source,
        odometer_km=odometer_km,
        total_cost=total_cost,
        notes=notes or None,
    )
    with Session(engine) as session:
        session.add(fill)
        session.commit()

    return RedirectResponse(url="/ui", status_code=303)

# =========================
# UI: Logout (Basic Auth "logout" trick)
# =========================
@ui.get("/logout", response_class=HTMLResponse)
def ui_logout():
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Logged out",
        headers={"WWW-Authenticate": "Basic"},
    )

# =========================
# UI: Reports (Transactions + Fuel)
# =========================
@ui.get("/report", response_class=HTMLResponse)
def ui_report(
    request: Request,
    year: int = date.today().year,
    source: str = "all",  # all | rental | work | personal
):
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    with Session(engine) as session:
        tx_query = select(Transaction).where(
            Transaction.tx_date >= start,
            Transaction.tx_date < end,
        )
        fuel_query = select(FuelLog).where(
            FuelLog.fill_date >= start,
            FuelLog.fill_date < end,
        )

        if source != "all":
            tx_query = tx_query.where(Transaction.source == source)
            fuel_query = fuel_query.where(FuelLog.source == source)

        txs = session.exec(tx_query).all()
        fuels = session.exec(fuel_query).all()

    by_month: dict[str, dict[str, float]] = {}
    by_category_expense: dict[str, float] = {}

    income_total = 0.0
    expense_total = 0.0

    # ---- Transactions ----
    for t in txs:
        month = t.tx_date.strftime("%Y-%m")
        by_month.setdefault(month, {"income": 0.0, "expense": 0.0, "net": 0.0})

        if t.type == "income":
            by_month[month]["income"] += float(t.amount)
            income_total += float(t.amount)
        else:
            by_month[month]["expense"] += float(t.amount)
            expense_total += float(t.amount)
            by_category_expense[t.category] = by_category_expense.get(t.category, 0.0) + float(t.amount)

        by_month[month]["net"] = by_month[month]["income"] - by_month[month]["expense"]

    # ---- Fuel logs (count as expense in category "Fuel") ----
    for f in fuels:
        month = f.fill_date.strftime("%Y-%m")
        by_month.setdefault(month, {"income": 0.0, "expense": 0.0, "net": 0.0})

        by_month[month]["expense"] += float(f.total_cost)
        expense_total += float(f.total_cost)

        by_category_expense["Fuel"] = by_category_expense.get("Fuel", 0.0) + float(f.total_cost)

        by_month[month]["net"] = by_month[month]["income"] - by_month[month]["expense"]

    # Sorted categories for table + pie chart
    by_category_sorted = sorted(
        ((k, round(v, 2)) for k, v in by_category_expense.items()),
        key=lambda x: x[0],
    )

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "year": year,
            "source": source,
            "source_label": "All" if source == "all" else source_label(source),
            "months": sorted(by_month.items()),
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "net_total": round(income_total - expense_total, 2),
            "by_category": by_category_sorted,
            "pie_labels": [k for k, _ in by_category_sorted],
            "pie_values": [v for _, v in by_category_sorted],
        },
    )

# =========================
# UI: Export CSV (includes fuel)
# =========================
@ui.get("/export.csv")
def export_csv(
    year: int = date.today().year,
    source: str = "all",
):
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    with Session(engine) as session:
        tx_query = select(Transaction).where(
            Transaction.tx_date >= start,
            Transaction.tx_date < end,
        )
        fuel_query = select(FuelLog).where(
            FuelLog.fill_date >= start,
            FuelLog.fill_date < end,
        )

        if source != "all":
            tx_query = tx_query.where(Transaction.source == source)
            fuel_query = fuel_query.where(FuelLog.source == source)

        txs = session.exec(tx_query).all()
        fuels = session.exec(fuel_query).all()

    output = io.StringIO()
    writer = csv.writer(output)

    # unified export columns
    writer.writerow(["date", "type", "source", "category", "amount", "vendor", "notes", "odometer_km"])

    # Transactions
    for t in txs:
        writer.writerow([
            t.tx_date.isoformat(),
            t.type,
            t.source,
            t.category,
            f"{float(t.amount):.2f}",
            t.vendor or "",
            t.notes or "",
            "",
        ])

    # Fuel rows as expenses
    for f in fuels:
        writer.writerow([
            f.fill_date.isoformat(),
            "expense",
            f.source,
            "Fuel",
            f"{float(f.total_cost):.2f}",
            "",
            f.notes or "",
            str(f.odometer_km),
        ])

    output.seek(0)

    filename = f"taxable_{year}_{source}.csv" if source != "all" else f"taxable_{year}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# =========================
# Register Router (LAST LINE)
# =========================
app.include_router(ui)
