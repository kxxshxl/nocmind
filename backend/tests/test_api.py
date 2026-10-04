import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_health_and_metrics(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    assert "X-Request-ID" in r.headers
    metrics = client.get("/metrics").text
    assert "nocmind_requests_total" in metrics


def test_authentication_required(client):
    assert client.get("/api/v1/incidents").status_code == 401
    assert client.get("/api/v1/incidents", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_full_flow_with_roles_and_approval_gate(client):
    admin = login(client, "admin", "nocmind-admin")
    operator = login(client, "operator", "operator-test-pw")
    viewer = login(client, "viewer", "viewer-test-pw")

    assert client.post("/api/v1/simulator/scenarios/router-overload", headers=viewer).status_code == 403
    assert client.post("/api/v1/simulator/scenarios/router-overload", headers=operator).status_code == 200
    inc = client.post("/api/v1/incidents", headers=operator, json={
        "title": "router overload", "region": "Mumbai", "severity": "SEV-2", "service": "API Gateway",
        "symptoms": ["high latency", "packet loss", "router saturation"]}).json()

    result = client.post(f"/api/v1/incidents/{inc['id']}/investigate", headers=operator).json()
    assert result["status"] == "REMEDIATION_PENDING"
    assert result["rca"]["root_cause"] == "edge router overload"
    assert result["remediation"]["requires_approval"] is True

    # Execution without approval is refused; viewers cannot approve.
    assert client.post(f"/api/v1/incidents/{inc['id']}/execute-remediation", headers=operator).status_code == 400
    assert client.post(f"/api/v1/incidents/{inc['id']}/approve-remediation", headers=viewer).status_code == 403

    assert client.post(f"/api/v1/incidents/{inc['id']}/approve-remediation", headers=operator).status_code == 200
    done = client.post(f"/api/v1/incidents/{inc['id']}/execute-remediation", headers=operator).json()
    assert done["success"] is True and done["status"] == "RESOLVED"
    assert done["validation"]["checks"]["packet_loss_under_2"] is True

    # Audit trail is admin-only and records the approval chain.
    assert client.get("/api/v1/auth/audit", headers=operator).status_code == 403
    actions = [a["action"] for a in client.get("/api/v1/auth/audit", headers=admin).json() if a["incident_id"] == inc["id"]]
    assert {"create_incident", "investigate", "approve_remediation", "execute_remediation"} <= set(actions)


def test_input_validation(client):
    operator = login(client, "operator", "operator-test-pw")
    r = client.post("/api/v1/incidents", headers=operator, json={"title": "x", "region": "Mumbai", "service": "API", "severity": "CRITICAL"})
    assert r.status_code == 422


def test_login_lockout(client):
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"username": "viewer", "password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "viewer", "password": "viewer-test-pw"}).status_code == 429
