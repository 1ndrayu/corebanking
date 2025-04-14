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
DATABASE_URL = "postgresql://postgres:postgres@localhost/corebanking"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
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
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    new_user = User(**user.dict())
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

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
