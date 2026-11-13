import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from app.core.exceptions import (
    DomainException,
    InsufficientPermissionException,
    TenantMismatchException,
    domain_exception_handler,
)


@pytest.fixture
def error_test_app():
    test_app = FastAPI()
    test_app.add_exception_handler(DomainException, domain_exception_handler)

    @test_app.get("/trigger-tenant-mismatch")
    async def trigger_mismatch():
        raise TenantMismatchException()

    @test_app.get("/trigger-permission-error")
    async def trigger_perm():
        raise InsufficientPermissionException(permission_code="scores:approve", scope="Science")

    return test_app


@pytest.mark.asyncio
async def test_tenant_mismatch_exception_response(error_test_app):
    transport = ASGITransport(app=error_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/trigger-tenant-mismatch")
        assert res.status_code == 403
        data = res.json()
        assert "error" in data
        assert data["error"]["code"] == "TENANT_MISMATCH"
        assert "school" in data["error"]["message"].lower()
        assert data["error"]["path"] == "/trigger-tenant-mismatch"


@pytest.mark.asyncio
async def test_insufficient_permission_response(error_test_app):
    transport = ASGITransport(app=error_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/trigger-permission-error")
        assert res.status_code == 403
        data = res.json()
        assert "error" in data
        assert data["error"]["code"] == "INSUFFICIENT_PERMISSIONS"
        assert "approve scores" in data["error"]["message"].lower()
        assert "science" in data["error"]["message"].lower()
        assert data["error"]["details"][0]["permission_required"] == "scores:approve"


@pytest.mark.asyncio
async def test_scores_incomplete_friendly_message():
    from app.core.exceptions import ScoresIncompleteException
    exc = ScoresIncompleteException(subject_name="Chemistry", missing_count=3)
    assert exc.status_code == 422
    assert exc.code == "SCORES_INCOMPLETE"
    assert "3 student(s) in Chemistry are still missing a score" in exc.message
    assert "mark them as absent or exempt" in exc.message
