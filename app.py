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
# App + Auth
# =========================
app = FastAPI(title="Taxable Tracker (Single User)")

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

# Protect ALL /ui routes automatically
ui = APIRouter(prefix="/ui", dependencies=[Depends(require_login)])
@ui.get("/logout", response_class=HTMLResponse)
def ui_logout():
    # Forces the browser to re-prompt for credentials next time
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Logged out",
        headers={"WWW-Authenticate": "Basic"},
    )

# =========================
# Database
# =========================
db_url = os.getenv("DATABASE_URL", "sqlite:///tracker.db")

# Render / Railway fix
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(db_url, echo=False)
templates = Jinja2Templates(directory="templates")

# =========================
# Models
# =========================
class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tx_date: date
    type: str  # income | expense
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

# =========================
# Startup
# =========================
@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

# =========================
# Root
# =========================
@app.get("/")
def root():
    return {"status": "ok", "ui": "/ui"}

# =========================
# UI ROUTES (PROTECTED)
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


@ui.get("/transaction/new", response_class=HTMLResponse)
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


@ui.post("/transaction/new")
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


@ui.get("/fuel/new", response_class=HTMLResponse)
def ui_new_fuel(request: Request):
    return templates.TemplateResponse("new_fuel.html", {"request": request})


@ui.post("/fuel/new")
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


@ui.get("/report", response_class=HTMLResponse)
def ui_report(request: Request, year: int = date.today().year):
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).where(
                Transaction.tx_date >= start,
                Transaction.tx_date < end,
            )
        ).all()

    by_month = {}
    by_category_expense = {}
    income_total = 0.0
    expense_total = 0.0

    for t in txs:
        month = t.tx_date.strftime("%Y-%m")
        by_month.setdefault(month, {"income": 0.0, "expense": 0.0, "net": 0.0})

        if t.type == "income":
            by_month[month]["income"] += t.amount
            income_total += t.amount
        else:
            by_month[month]["expense"] += t.amount
            expense_total += t.amount
            by_category_expense[t.category] = by_category_expense.get(t.category, 0.0) + t.amount

        by_month[month]["net"] = by_month[month]["income"] - by_month[month]["expense"]

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "year": year,
            "months": sorted(by_month.items()),
            "income_total": round(income_total, 2),
            "expense_total": round(expense_total, 2),
            "net_total": round(income_total - expense_total, 2),
            "by_category": sorted(
                ((k, round(v, 2)) for k, v in by_category_expense.items()),
                key=lambda x: x[0],
            ),
        },
    )


@ui.get("/export.csv")
def export_csv(year: int = date.today().year):
    start = date(year, 1, 1)
    end = date(year + 1, 1, 1)

    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).where(
                Transaction.tx_date >= start,
                Transaction.tx_date < end,
            )
        ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "type", "category", "amount", "vendor", "notes"])

    for t in txs:
        writer.writerow([
            t.tx_date.isoformat(),
            t.type,
            t.category,
            f"{t.amount:.2f}",
            t.vendor or "",
            t.notes or "",
        ])

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="taxable_{year}.csv"'},
    )

# IMPORTANT: register router at the end
app.include_router(ui)
