from fastapi import FastAPI, HTTPException, Depends, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, UUID4, Field, condecimal
from sqlalchemy import create_engine, Column, String, DateTime, ForeignKey, Numeric, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from uuid import uuid4
from enum import Enum as PyEnum
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from typing import Optional
import os

# ───── DATABASE SETUP ─────
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://default_user:default_pass@localhost/corebanking")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ───── SECURITY CONFIG ─────
SECRET_KEY = "your-secret-key"  # Use environment variable in production
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/token")

# ───── ENUMS ─────
class AccountType(str, PyEnum):
    savings = "savings"
    current = "current"
    loan = "loan"

class TransactionType(str, PyEnum):
    deposit = "deposit"
    withdraw = "withdraw"
    transfer = "transfer"

# ───── MODELS ─────
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    full_name = Column(String)
    email = Column(String, unique=True)
    hashed_password = Column(String)
    phone = Column(String)
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

# ───── SCHEMAS ─────
class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
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

class Token(BaseModel):
    access_token: str
    token_type: str

# ───── UTILS ─────
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def hash_password(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(status_code=401, detail="Invalid token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise credentials_exception
    return user

# ───── APP SETUP ─────
app = FastAPI()

@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

# ───── AUTH ─────
@app.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

# ───── USERS ─────
@app.post("/users")
def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    new_user = User(
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        hashed_password=hash_password(user.password)
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"id": new_user.id, "email": new_user.email}

@app.get("/users/me")
def read_users_me(current_user: User = Depends(get_current_user)):
    return {"id": current_user.id, "email": current_user.email}

# ───── ACCOUNTS ─────
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
    db.add(Balance(account_id=acc.id))
    db.commit()
    return acc

@app.get("/accounts/{account_id}/balance")
def get_balance(account_id: UUID4, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    bal = db.query(Balance).filter_by(account_id=str(account_id)).first()
    if not bal:
        raise HTTPException(404, "Balance not found")
    return bal

# ───── TRANSACTIONS ─────
@app.post("/transactions")
def make_transaction(txn: TransactionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if txn.txn_type == "transfer":
        from_bal = db.query(Balance).filter_by(account_id=str(txn.from_account_id)).first()
        to_bal = db.query(Balance).filter_by(account_id=str(txn.to_account_id)).first()
        if from_bal.available_balance < txn.amount:
            raise HTTPException(400, "Insufficient balance")
        from_bal.available_balance -= txn.amount
        to_bal.available_balance += txn.amount

    elif txn.txn_type == "deposit":
        to_bal = db.query(Balance).filter_by(account_id=str(txn.to_account_id)).first()
        to_bal.available_balance += txn.amount

    elif txn.txn_type == "withdraw":
        from_bal = db.query(Balance).filter_by(account_id=str(txn.from_account_id)).first()
        if from_bal.available_balance < txn.amount:
            raise HTTPException(400, "Insufficient funds")
        from_bal.available_balance -= txn.amount

    transaction = Transaction(**txn.dict())
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction
