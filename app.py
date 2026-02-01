import os
from datetime import date
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import SQLModel, Field, create_engine, Session, select

# -------------------------
# Config
# -------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tracker.db")
RECEIPTS_DIR = os.getenv("RECEIPTS_DIR", "./receipts")

os.makedirs(RECEIPTS_DIR, exist_ok=True)

engine = create_engine(DATABASE_URL, echo=False)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# -------------------------
# Models
# -------------------------
class Transaction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    tx_date: date
    type: str
    source: str
    category: str
    amount: float
    vendor: str | None = None
    notes: str | None = None
    receipt: str | None = None


class Fuel(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    fill_date: date
    odometer_km: int
    total_cost: float
    notes: str | None = None
    receipt: str | None = None


SQLModel.metadata.create_all(engine)

# -------------------------
# Routes
# -------------------------
@app.get("/")
def root():
    return {"status": "ok", "ui": "/ui"}


@app.get("/ui", response_class=HTMLResponse)
def home(request: Request):
    with Session(engine) as session:
        txs = session.exec(
            select(Transaction).order_by(Transaction.tx_date.desc())
        ).all()
    return templates.TemplateResponse("home.html", {
        "request": request,
        "transactions": txs
    })


@app.get("/ui/transaction/new", response_class=HTMLResponse)
def new_transaction(request: Request):
    return templates.TemplateResponse("new_transaction.html", {
        "request": request,
        "default_type": "expense",
        "default_category": ""
    })


@app.post("/ui/transaction/new")
async def save_transaction(
    request: Request,
    tx_date: date = Form(...),
    type: str = Form(...),
    source: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    vendor: str = Form(None),
    notes: str = Form(None),
    receipt: UploadFile | None = File(None),
):
    receipt_name = None

    if receipt and type == "expense":
        safe_name = receipt.filename.replace(" ", "_")
        receipt_name = f"{tx_date}_{safe_name}"
        path = os.path.join(RECEIPTS_DIR, receipt_name)

        with open(path, "wb") as f:
            f.write(await receipt.read())

    with Session(engine) as session:
        tx = Transaction(
            tx_date=tx_date,
            type=type,
            source=source,
            category=category,
            amount=amount,
            vendor=vendor,
            notes=notes,
            receipt=receipt_name
        )
        session.add(tx)
        session.commit()

    return RedirectResponse("/ui", status_code=303)


@app.get("/ui/fuel/new", response_class=HTMLResponse)
def new_fuel(request: Request):
    return templates.TemplateResponse("new_fuel.html", {
        "request": request
    })


@app.post("/ui/fuel/new")
async def save_fuel(
    fill_date: date = Form(...),
    odometer_km: int = Form(...),
    total_cost: float = Form(...),
    notes: str = Form(None),
    receipt: UploadFile | None = File(None),
):
    receipt_name = None

    if receipt:
        safe_name = receipt.filename.replace(" ", "_")
        receipt_name = f"{fill_date}_{safe_name}"
        path = os.path.join(RECEIPTS_DIR, receipt_name)

        with open(path, "wb") as f:
            f.write(await receipt.read())

    with Session(engine) as session:
        fuel = Fuel(
            fill_date=fill_date,
            odometer_km=odometer_km,
            total_cost=total_cost,
            notes=notes,
            receipt=receipt_name
        )
        session.add(fuel)
        session.commit()

    return RedirectResponse("/ui", status_code=303)
