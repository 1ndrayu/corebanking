import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from main import app, get_db, User, Account, create_access_token

@pytest.fixture(scope="module")
def client():
    """Fixture to create and provide a TestClient for FastAPI app."""
    client = TestClient(app)
    yield client


@pytest.fixture(scope="module")
async def db_session():
    """Fixture to provide a temporary database session."""
    async with get_db() as db:
        yield db


@pytest.fixture
async def create_user(db_session: AsyncSession):
    """Fixture to create a user in the database."""
    user = User(username="testuser", hashed_password="hashedpassword")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def create_account(db_session: AsyncSession, create_user):
    """Fixture to create an account for the created user."""
    account = Account(user_id=create_user.id, balance=1000.0)
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


def test_create_user(client):
    """Test user creation."""
    user_data = {"username": "newuser", "password": "newpassword"}
    response = client.post("/users/", json=user_data)

    assert response.status_code == 200
    assert response.json()["username"] == "newuser"


def test_login(client, create_user):
    """Test login functionality."""
    login_data = {"username": "testuser", "password": "wrongpassword"}
    response = client.post("/login", data=login_data)

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect username or password"

    login_data["password"] = "hashedpassword"  # Correct password
    response = client.post("/login", data=login_data)

    assert response.status_code == 200
    assert "access_token" in response.json()


def test_transfer_funds(client, create_account):
    """Test fund transfer functionality."""
    transfer_data = {
        "from_account": create_account.id,
        "to_account": 9999,  # Assuming this account doesn't exist
        "amount": 100.0,
    }
    response = client.post("/transfer/", json=transfer_data, headers={
        "Authorization": f"Bearer {create_access_token({'sub': 'testuser'})}"
    })

    assert response.status_code == 404
    assert response.json()["detail"] == "Account not found"

    # Create another account for the transfer test
    to_account = Account(user_id=create_account.user_id, balance=500.0)
    create_account.db_session.add(to_account)
    await create_account.db_session.commit()

    transfer_data["to_account"] = to_account.id
    response = client.post("/transfer/", json=transfer_data, headers={
        "Authorization": f"Bearer {create_access_token({'sub': 'testuser'})}"
    })

    assert response.status_code == 200
    assert response.json()["message"] == "Transfer successful"


def test_get_user_accounts(client, create_account):
    """Test retrieving user accounts."""
    response = client.get(f"/users/testuser/accounts", headers={
        "Authorization": f"Bearer {create_access_token({'sub': 'testuser'})}"
    })

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["balance"] == 1000.0


@pytest.mark.asyncio
async def test_database_insertion(db_session: AsyncSession):
    """Test direct database insertions."""
    user = User(username="dbuser", hashed_password="dbpassword")
    db_session.add(user)
    await db_session.commit()

    query = select(User).filter(User.username == "dbuser")
    result = await db_session.execute(query)
    assert result.scalar_one().username == "dbuser"
