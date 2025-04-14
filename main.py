from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, EmailStr, UUID4, Field, condecimal
from sqlalchemy import create_engine, Column, String, DateTime, ForeignKey, Numeric, Enum, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from uuid import uuid4
from enum import Enum as PyEnum
from datetime import datetime
from typing import Optional, List

# ───── DATABASE SETUP ───────────────────────────────────────
import os
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://default_user:default_pass@localhost/corebanking")
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
engine = create_async_engine(DATABASE_URL, future=True)
async_session = sessionmaker(engine, class_=AsyncSession)
Base = declarative_base()

# ───── ENUMS ────────────────────────────────────────────────
class AccountType(str, PyEnum):
    savings = "savings"
    current = "current"
    loan = "loan"

class TransactionType(str, PyEnum):
    deposit = "deposit"
    withdraw = "withdraw"
    transfer = "transfer"
    chargeback = "chargeback"

class KYCStatus(str, PyEnum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"

class FlagType(str, PyEnum):
    fraud_suspect = "fraud_suspect"
    high_risk = "high_risk"
    over_limit = "over_limit"


# ───── MODELS ───────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    full_name = Column(String)
    email = Column(String, unique=True)
    phone = Column(String)
    kyc_status = Column(String, default="pending")
    created_at = Column(DateTime, default=func.now())


class Account(Base):
    __tablename__ = "accounts"
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    user_id = Column(String, ForeignKey("users.id"))
    account_type = Column(String)
    account_number = Column(String, unique=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=func.now())


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    from_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    to_account_id = Column(String, ForeignKey("accounts.id"), nullable=True)
    amount = Column(Numeric(12, 2))
    txn_type = Column(String)
    reference_note = Column(String)
    status = Column(String, default="success")
    created_at = Column(DateTime, default=func.now())


class Balance(Base):
    __tablename__ = "balances"
    account_id = Column(String, ForeignKey("accounts.id"), primary_key=True)
    available_balance = Column(Numeric(12, 2), default=0)
    last_updated = Column(DateTime, default=func.now())


class MLFlag(Base):
    __tablename__ = "ml_flags"
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    txn_id = Column(String, ForeignKey("transactions.id"))
    flag_type = Column(String)
    model_score = Column(Numeric)
    is_resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())


# ───── SCHEMAS ──────────────────────────────────────────────
class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: Optional[str]

class AccountCreate(BaseModel):
    user_id: UUID4
    account_type: AccountType

class TransactionCreate(BaseModel):
    from_account_id: Optional[UUID4]
    to_account_id: Optional[UUID4]
    amount: condecimal(gt=0)
    txn_type: TransactionType
    reference_note: Optional[str] = None

# ───── FASTAPI SETUP ────────────────────────────────────────
app = FastAPI()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ───── USER ENDPOINTS ──────────────────────────────────────
@app.post("/users")
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    async with db.begin():
        new_user = User(**user.dict())
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    return new_user

from fastapi.security import OAuth2PasswordBearer
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.get("/users/me")
async def read_users_me(token: str = Depends(oauth2_scheme)):
    # Decode and validate JWT token here
    return {"token": token}

# ───── ACCOUNT ENDPOINTS ───────────────────────────────────
@app.post("/accounts")
def create_account(account: AccountCreate, db: Session = Depends(get_db)):
    account_number = str(uuid4().int)[0:12]
    acc = Account(
        user_id=str(account.user_id),
        account_type=account.account_type,
        account_number=account_number,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    # initialize balance
    db.add(Balance(account_id=acc.id, available_balance=0))
    db.commit()
    return acc

# ───── TRANSACTION ENDPOINTS ───────────────────────────────
@app.post("/transactions")
def make_transaction(txn: TransactionCreate, db: Session = Depends(get_db)):
    # Basic balance checks
    if txn.txn_type == "transfer":
        if not txn.from_account_id or not txn.to_account_id:
            raise HTTPException(400, "Transfer requires from and to accounts")
        from_balance = db.query(Balance).filter_by(account_id=str(txn.from_account_id)).first()
        if from_balance.available_balance < txn.amount:
            raise HTTPException(400, "Insufficient balance")

        from_balance.available_balance -= txn.amount
        to_balance = db.query(Balance).filter_by(account_id=str(txn.to_account_id)).first()
        to_balance.available_balance += txn.amount

    elif txn.txn_type == "deposit":
        to_balance = db.query(Balance).filter_by(account_id=str(txn.to_account_id)).first()
        to_balance.available_balance += txn.amount

    elif txn.txn_type == "withdraw":
        from_balance = db.query(Balance).filter_by(account_id=str(txn.from_account_id)).first()
        if from_balance.available_balance < txn.amount:
            raise HTTPException(400, "Insufficient funds")
        from_balance.available_balance -= txn.amount

    transaction = Transaction(**txn.dict())
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction

# ───── BALANCE LOOKUP ──────────────────────────────────────
@app.get("/accounts/{account_id}/balance")
def get_balance(account_id: UUID4, db: Session = Depends(get_db)):
    bal = db.query(Balance).filter_by(account_id=str(account_id)).first()
    if not bal:
        raise HTTPException(404, "Balance record not found")
    return bal

# ───── INIT SCHEMA ON START ────────────────────────────────
@app.on_event("startup")
def create_tables():
    Base.metadata.create_all(bind=engine)

# ───── ERROR HANDLING ────────────────────────────────
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI()

class CustomException(Exception):
    def __init__(self, name: str):
        self.name = name

@app.exception_handler(CustomException)
async def custom_exception_handler(request: Request, exc: CustomException):
    return JSONResponse(
        status_code=400,
        content={"message": f"Something went wrong: {exc.name}"},
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.detail},
    )

# ───── TRANSACTION VALIDATION ────────────────────────────────
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException
from sqlalchemy import select
from models import Account, Transaction, Balance

# This is the route for transferring funds between accounts
@app.post("/transactions/transfer/")
async def transfer_funds(from_account_id: int, to_account_id: int, amount: float, db: Session = Depends(get_db)):
    # Check if accounts exist
    from_account = db.execute(select(Account).filter(Account.id == from_account_id)).scalar()
    to_account = db.execute(select(Account).filter(Account.id == to_account_id)).scalar()

    if not from_account or not to_account:
        raise HTTPException(status_code=404, detail="One or both accounts not found")
    
    # Check if there are sufficient funds in the 'from_account'
    balance = db.execute(select(Balance).filter(Balance.account_id == from_account_id)).scalar()
    if balance and balance.available_balance < amount:
        raise HTTPException(status_code=400, detail="Insufficient funds")
    
    # Deduct funds from the sender account
    balance.available_balance -= amount
    db.commit()

    # Add funds to the recipient account
    recipient_balance = db.execute(select(Balance).filter(Balance.account_id == to_account_id)).scalar()
    if not recipient_balance:
        raise HTTPException(status_code=404, detail="Recipient account not found")
    
    recipient_balance.available_balance += amount
    db.commit()

    # Create the transaction record
    transaction = Transaction(from_account_id=from_account_id, to_account_id=to_account_id, amount=amount)
    db.add(transaction)
    db.commit()

    return {"message": "Transfer successful", "transaction_id": transaction.id}

# ───── DAILY TRANSACTION LIMITS ────────────────────────────────
from datetime import datetime, timedelta

@app.post("/transactions/transfer/")
async def transfer_funds_with_limit(from_account_id: int, to_account_id: int, amount: float, db: Session = Depends(get_db)):
    # Fetch account data
    from_account = db.execute(select(Account).filter(Account.id == from_account_id)).scalar()
    to_account = db.execute(select(Account).filter(Account.id == to_account_id)).scalar()

    if not from_account or not to_account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Check for daily transaction limit (example limit is 5000)
    today = datetime.utcnow().date()
    total_today = db.execute(
        select(Transaction)
        .filter(Transaction.from_account_id == from_account_id, Transaction.created_at >= today)
    ).all()
    
    daily_limit = 100000  # Set daily limit
    total_transferred_today = sum(txn.amount for txn in total_today)

    if total_transferred_today + amount > daily_limit:
        raise HTTPException(status_code=400, detail="Daily transaction limit exceeded")

    # Check for sufficient balance
    balance = db.execute(select(Balance).filter(Balance.account_id == from_account_id)).scalar()
    if balance and balance.available_balance < amount:
        raise HTTPException(status_code=400, detail="Insufficient funds")

    # Process the transaction (same as before)
    balance.available_balance -= amount
    db.commit()

    recipient_balance = db.execute(select(Balance).filter(Balance.account_id == to_account_id)).scalar()
    recipient_balance.available_balance += amount
    db.commit()

    transaction = Transaction(from_account_id=from_account_id, to_account_id=to_account_id, amount=amount)
    db.add(transaction)
    db.commit()

    return {"message": "Transfer successful", "transaction_id": transaction.id}

