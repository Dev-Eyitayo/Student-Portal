import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

ALLOWED_DOMAINS = {
    "identity",
    "structure",
    "students",
    "enrollments",
    "subjects",
    "class-subjects",
    "assessments",
    "computation",
    "publication",
    "results",
    "audit",
    "health",
}


def _api_paths() -> set[str]:
    return {path for path in app.openapi()["paths"] if path.startswith("/api/")}


def test_api_routes_follow_domain_namespaces():
    api_paths = _api_paths()

    assert api_paths
    for path in api_paths:
        assert path.startswith("/api/v1/"), path
        domain = path.split("/")[3]
        assert domain in ALLOWED_DOMAINS, f"{path} uses an unknown API domain '{domain}'"


def test_legacy_flat_routes_are_not_registered():
    api_paths = _api_paths()

    assert "/api/v1/sessions" not in api_paths
    assert "/api/v1/class-groups" not in api_paths
    assert "/api/v1/enrollments" not in api_paths


@pytest.mark.asyncio
async def test_authentication_errors_use_standard_error_envelope():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/identity/me")

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["path"] == "/api/v1/identity/me"
    assert "sign in" in body["error"]["message"].lower()
    assert "detail" not in body


@pytest.mark.asyncio
async def test_validation_errors_use_standard_error_envelope():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/identity/login", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["path"] == "/api/v1/identity/login"
    assert body["error"]["details"]
    assert "review the highlighted fields" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_success_responses_use_standard_success_envelope():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "healthy"
