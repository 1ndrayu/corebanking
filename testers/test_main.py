from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_create_user():
    response = client.post("/users/", json={"full_name": "John Doe", "email": "john@example.com", "phone": "1234567890"})
    assert response.status_code == 200
    assert "id" in response.json()

def test_create_account():
    response = client.post("/accounts/", json={"user_id": 1, "account_type": "savings"})
    assert response.status_code == 200
    assert "account_number" in response.json()
