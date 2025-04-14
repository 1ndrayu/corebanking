from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_create_user():
    response = client.post("/users/", json={"full_name": "Alice", "email": "alice@example.com", "phone": "1234567890"})
    assert response.status_code == 200
    assert "id" in response.json()

def test_transfer_insufficient_funds():
    # Test insufficient funds
    response = client.post("/transactions/transfer/", json={"from_account_id": 1, "to_account_id": 2, "amount": 1000})
    assert response.status_code == 400
    assert response.json() == {"message": "Insufficient funds"}

def test_daily_transaction_limit():
    # Test exceeding daily limit
    response = client.post("/transactions/transfer/", json={"from_account_id": 1, "to_account_id": 2, "amount": 6000})
    assert response.status_code == 400
    assert response.json() == {"message": "Daily transaction limit exceeded"}
