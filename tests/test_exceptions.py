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
        assert "tenant" in data["error"]["message"].lower()
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
        assert "scores:approve" in data["error"]["message"]
        assert "Science" in data["error"]["message"]
