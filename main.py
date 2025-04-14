import os
from datetime import datetime, timedelta
import logging

from fastapi import FastAPI, Depends, HTTPException, status, APIRouter
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.future import select
from dotenv import load_dotenv
from typing import List

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Secret and DB config from environment variables
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://username:password@localhost/corebanking")

# Database setup
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# FastAPI app
app = FastAPI()

# Password context and OAuth2
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Models
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    accounts = relationship("Account", back_populates="owner")

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    balance = Column(Float, default=0.0)
    owner = relationship("User", back_populates="accounts")

# Schemas
class UserCreate(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TransferRequest(BaseModel):
    from_account: int
    to_account: int
    amount: float

# Utility functions
def get_password_hash(password: str):
    """Hashes a plain password."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str):
    """Verifies if the plain password matches the hashed password."""
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Creates an access token (JWT) with the given data and expiry."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# Dependency to get DB session
async def get_db():
    """Database session generator."""
    async with AsyncSessionLocal() as db:
        yield db

# OAuth2 Password flow
@app.post("/login", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    """Login user and generate JWT token."""
    try:
        # Fetch user from the database
        user = await db.execute(select(User).filter(User.username == form_data.username))
        user = user.scalar_one_or_none()

        if not user or not verify_password(form_data.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
        
        # Create JWT token
        access_token = create_access_token(data={"sub": user.username})
        return {"access_token": access_token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Login failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

# Create user (register)
@app.post("/users/", response_model=UserCreate)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):
    """Create a new user with hashed password."""
    try:
        hashed_password = get_password_hash(user.password)
        db_user = User(username=user.username, hashed_password=hashed_password)

        db.add(db_user)
        await db.commit()
        await db.refresh(db_user)
        return db_user
    except Exception as e:
        logger.error(f"User creation failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

# Transfer funds between accounts
@app.post("/transfer/")
async def transfer_funds(transfer: TransferRequest, db: AsyncSession = Depends(get_db), token: str = Depends(oauth2_scheme)):
    """Transfer funds between two accounts."""
    try:
        # Check if the JWT token is valid
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username: str = payload.get("sub")
            if username is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

        # Retrieve accounts from database
        from_account = await db.execute(select(Account).filter(Account.id == transfer.from_account))
        from_account = from_account.scalar_one_or_none()
        to_account = await db.execute(select(Account).filter(Account.id == transfer.to_account))
        to_account = to_account.scalar_one_or_none()

        if not from_account or not to_account:
            raise HTTPException(status_code=404, detail="Account not found")

        if from_account.balance < transfer.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance")

        # Perform transfer
        from_account.balance -= transfer.amount
        to_account.balance += transfer.amount

        await db.commit()
        return {"message": "Transfer successful"}
    except HTTPException as e:
        logger.error(f"Transfer failed: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Internal error during transfer: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

# Get user accounts
@app.get("/users/{username}/accounts", response_model=List[Account])
async def get_user_accounts(username: str, db: AsyncSession = Depends(get_db)):
    """Get all accounts for a specific user."""
    try:
        user = await db.execute(select(User).filter(User.username == username))
        user = user.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        accounts = await db.execute(select(Account).filter(Account.user_id == user.id))
        return accounts.scalars().all()
    except Exception as e:
        logger.error(f"Failed to retrieve accounts: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error")

# On startup: create tables
@app.on_event("startup")
async def startup():
    """Create database tables on startup."""
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        logger.error(f"Database table creation failed: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to initialize database")

# Close DB connection on shutdown
@app.on_event("shutdown")
async def shutdown():
    """Close database connection on shutdown."""
    await engine.dispose()
