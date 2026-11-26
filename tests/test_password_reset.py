import asyncio
import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import async_session_factory
from app.core.redis import close_redis, get_redis
from app.main import app
from app.modules.identity import tasks as identity_tasks
from app.modules.identity.models import User
from app.modules.identity.seed import seed_system_permissions

settings = get_settings()


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)
    await close_redis()
    redis = get_redis()
    keys = [k async for k in redis.scan_iter("rl:pwd_reset:*")]
    if keys:
        await redis.delete(*keys)
    yield
    await close_redis()


async def _create_school_and_admin(client: AsyncClient, suffix: str) -> tuple[str, str, str]:
    email = f"admin.reset.{suffix}@olivet-test.edu.ng"
    password = "InitialPassword123!"
    payload = {
        "name": f"Olivet Reset School {suffix}",
        "slug": f"olivet-reset-{suffix}",
        "official_code": f"OBHS-RST-{suffix.upper()}",
        "state": "Oyo State",
        "city": "Oyo",
        "address": "Olivet Heights, Oyo, Nigeria",
        "country_code": "NG",
        "admin_email": email,
        "admin_first_name": "Adeyemi",
        "admin_last_name": "Olufemi",
        "admin_password": password,
    }
    response = await client.post("/api/v1/identity/schools", json=payload)
    assert response.status_code == 201, response.text
    school_id = response.json()["data"]["id"]
    return school_id, email, password


@pytest.mark.asyncio
async def test_password_reset_happy_path(monkeypatch):
    queued_resets: list[tuple] = []
    queued_alerts: list[tuple] = []

    class FakeResetEmailTask:
        def delay(self, *args, **kwargs):
            queued_resets.append(args)

    class FakeAlertEmailTask:
        def delay(self, *args, **kwargs):
            queued_alerts.append(args)

    monkeypatch.setattr(identity_tasks, "send_password_reset_email", FakeResetEmailTask())
    monkeypatch.setattr(identity_tasks, "send_password_changed_alert_email", FakeAlertEmailTask())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        test_ip = f"192.168.1.{int(suffix[:2], 16) % 256}"
        _, email, old_password = await _create_school_and_admin(client, suffix)

        # 1. Request forgot password
        forgot_resp = await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email},
            headers={"x-forwarded-for": test_ip},
        )
        assert forgot_resp.status_code == 200, forgot_resp.text
        assert forgot_resp.json()["success"] is True
        assert (
            forgot_resp.json()["data"]["message"]
            == "If an account exists for that email address, we've sent password reset instructions."
        )

        # Confirm Celery task was queued with raw token
        assert len(queued_resets) == 1
        to_email, first_name, raw_token = queued_resets[0]
        assert to_email == email
        assert first_name == "Adeyemi"
        assert len(raw_token) > 20

        # Confirm token is stored hashed in Redis (not raw)
        redis = get_redis()
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        stored_user_id = await redis.get(f"pwd_reset:token:{token_hash}")
        assert stored_user_id is not None
        assert await redis.get(f"pwd_reset:token:{raw_token}") is None

        # 2. Verify token
        verify_resp = await client.get(
            f"/api/v1/identity/reset-password/verify?token={raw_token}",
            headers={"x-forwarded-for": test_ip},
        )
        assert verify_resp.status_code == 200, verify_resp.text
        assert verify_resp.json()["data"]["valid"] is True
        # Verify does not leak PII
        assert "email" not in verify_resp.json()["data"]
        assert "user_id" not in verify_resp.json()["data"]

        # 3. Complete password reset
        new_password = "NewSuperSecretPassword123!"
        reset_resp = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": raw_token, "new_password": new_password},
            headers={"x-forwarded-for": test_ip},
        )
        assert reset_resp.status_code == 200, reset_resp.text
        assert reset_resp.json()["success"] is True
        assert (
            reset_resp.json()["data"]["message"]
            == "Your password has been reset. You can now sign in with your new password."
        )

        # Confirm alert email was queued
        assert len(queued_alerts) == 1
        assert queued_alerts[0][0] == email

        # Confirm Redis keys are consumed
        assert await redis.get(f"pwd_reset:token:{token_hash}") is None
        assert await redis.get(f"pwd_reset:user:{stored_user_id}") is None

        # 4. Old password fails
        old_login = await client.post(
            "/api/v1/identity/login",
            json={"email": email, "password": old_password},
        )
        assert old_login.status_code == 401

        # 5. New password succeeds
        new_login = await client.post(
            "/api/v1/identity/login",
            json={"email": email, "password": new_password},
        )
        assert new_login.status_code == 200
        assert "access_token" in new_login.json()["data"]


@pytest.mark.asyncio
async def test_password_reset_invalidates_prior_sessions(monkeypatch):
    queued_resets: list[tuple] = []

    class FakeResetEmailTask:
        def delay(self, *args, **kwargs):
            queued_resets.append(args)

    class FakeAlertEmailTask:
        def delay(self, *args, **kwargs):
            pass

    monkeypatch.setattr(identity_tasks, "send_password_reset_email", FakeResetEmailTask())
    monkeypatch.setattr(identity_tasks, "send_password_changed_alert_email", FakeAlertEmailTask())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        _, email, old_password = await _create_school_and_admin(client, suffix)

        # Log in before reset to obtain active access & refresh tokens
        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": email, "password": old_password},
        )
        assert login_resp.status_code == 200
        old_access_token = login_resp.json()["data"]["access_token"]
        old_refresh_token = login_resp.json()["data"]["refresh_token"]

        # Ensure session works initially
        me_resp = await client.get(
            "/api/v1/identity/me",
            headers={"Authorization": f"Bearer {old_access_token}"},
        )
        assert me_resp.status_code == 200

        # Wait across integer-second boundary so token iat < token_valid_after
        await asyncio.sleep(1.05)

        # Request and perform reset
        await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email},
            headers={"x-forwarded-for": "10.0.0.1"},
        )
        assert len(queued_resets) == 1
        raw_token = queued_resets[0][2]

        reset_resp = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": raw_token, "new_password": "NewReplacementPassword123!"},
            headers={"x-forwarded-for": "10.0.0.1"},
        )
        assert reset_resp.status_code == 200

        # Old access token must now be rejected
        me_after_resp = await client.get(
            "/api/v1/identity/me",
            headers={"Authorization": f"Bearer {old_access_token}"},
        )
        assert me_after_resp.status_code == 401

        # Old refresh token must now be rejected
        refresh_after_resp = await client.post(
            "/api/v1/identity/refresh",
            json={"refresh_token": old_refresh_token},
        )
        assert refresh_after_resp.status_code == 401


@pytest.mark.asyncio
async def test_password_reset_single_use_enforcement(monkeypatch):
    queued_resets: list[tuple] = []

    class FakeResetEmailTask:
        def delay(self, *args, **kwargs):
            queued_resets.append(args)

    class FakeAlertEmailTask:
        def delay(self, *args, **kwargs):
            pass

    monkeypatch.setattr(identity_tasks, "send_password_reset_email", FakeResetEmailTask())
    monkeypatch.setattr(identity_tasks, "send_password_changed_alert_email", FakeAlertEmailTask())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        _, email, _ = await _create_school_and_admin(client, suffix)

        await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email},
            headers={"x-forwarded-for": "10.0.0.2"},
        )
        raw_token = queued_resets[0][2]

        # First consumption succeeds
        resp1 = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": raw_token, "new_password": "NewSecretPassword123!"},
            headers={"x-forwarded-for": "10.0.0.2"},
        )
        assert resp1.status_code == 200

        # Second consumption fails with 422 and friendly message
        resp2 = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": raw_token, "new_password": "AnotherNewPassword123!"},
            headers={"x-forwarded-for": "10.0.0.2"},
        )
        assert resp2.status_code == 422
        assert "invalid or has expired" in resp2.json()["error"]["message"]

        # Verifying consumed token fails
        verify_resp = await client.get(
            f"/api/v1/identity/reset-password/verify?token={raw_token}",
            headers={"x-forwarded-for": "10.0.0.2"},
        )
        assert verify_resp.status_code == 422


@pytest.mark.asyncio
async def test_password_reset_superseding_token(monkeypatch):
    queued_resets: list[tuple] = []

    class FakeResetEmailTask:
        def delay(self, *args, **kwargs):
            queued_resets.append(args)

    class FakeAlertEmailTask:
        def delay(self, *args, **kwargs):
            pass

    monkeypatch.setattr(identity_tasks, "send_password_reset_email", FakeResetEmailTask())
    monkeypatch.setattr(identity_tasks, "send_password_changed_alert_email", FakeAlertEmailTask())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        _, email, _ = await _create_school_and_admin(client, suffix)
        redis = get_redis()

        # Request 1
        await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email},
            headers={"x-forwarded-for": "10.0.0.3"},
        )
        assert len(queued_resets) == 1
        token_1 = queued_resets[0][2]

        # Clear cooldown so second request is allowed immediately
        email_hash = hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()
        await redis.delete(f"rl:pwd_reset:cooldown:{email_hash}")

        # Request 2
        await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email},
            headers={"x-forwarded-for": "10.0.0.3"},
        )
        assert len(queued_resets) == 2
        token_2 = queued_resets[1][2]
        assert token_1 != token_2

        # Token 1 must be invalid
        verify_1 = await client.get(
            f"/api/v1/identity/reset-password/verify?token={token_1}",
            headers={"x-forwarded-for": "10.0.0.3"},
        )
        assert verify_1.status_code == 422

        reset_1 = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": token_1, "new_password": "NewPasswordFromToken1!"},
            headers={"x-forwarded-for": "10.0.0.3"},
        )
        assert reset_1.status_code == 422

        # Token 2 must be valid and succeed
        verify_2 = await client.get(
            f"/api/v1/identity/reset-password/verify?token={token_2}",
            headers={"x-forwarded-for": "10.0.0.3"},
        )
        assert verify_2.status_code == 200

        reset_2 = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": token_2, "new_password": "NewPasswordFromToken2!"},
            headers={"x-forwarded-for": "10.0.0.3"},
        )
        assert reset_2.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_anti_enumeration(monkeypatch):
    queued_resets: list[tuple] = []

    class FakeResetEmailTask:
        def delay(self, *args, **kwargs):
            queued_resets.append(args)

    monkeypatch.setattr(identity_tasks, "send_password_reset_email", FakeResetEmailTask())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case A: Unknown email
        resp_unknown = await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": "nonexistent.user.xyz@olivet-test.edu.ng"},
            headers={"x-forwarded-for": "10.0.0.4"},
        )
        assert resp_unknown.status_code == 200
        assert (
            resp_unknown.json()["data"]["message"]
            == "If an account exists for that email address, we've sent password reset instructions."
        )
        assert len(queued_resets) == 0

        # Case B: Deactivated user (is_active=False)
        suffix = uuid.uuid4().hex[:6]
        _, email, _ = await _create_school_and_admin(client, suffix)

        async with async_session_factory() as session:
            stmt = select(User).where(User.email == email)
            user = (await session.execute(stmt)).scalar_one()
            user.is_active = False
            await session.commit()

        # Clear cooldown so we can test the deactivated user
        email_hash = hashlib.sha256(email.lower().strip().encode("utf-8")).hexdigest()
        redis = get_redis()
        await redis.delete(f"rl:pwd_reset:cooldown:{email_hash}")

        resp_deactivated = await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email},
            headers={"x-forwarded-for": "10.0.0.4"},
        )
        assert resp_deactivated.status_code == 200
        assert (
            resp_deactivated.json()["data"]["message"]
            == "If an account exists for that email address, we've sent password reset instructions."
        )
        assert len(queued_resets) == 0

        # Case C: Soft-deleted user (deleted_at is not None)
        suffix2 = uuid.uuid4().hex[:6]
        _, email2, _ = await _create_school_and_admin(client, suffix2)

        async with async_session_factory() as session:
            stmt = select(User).where(User.email == email2)
            user2 = (await session.execute(stmt)).scalar_one()
            user2.deleted_at = datetime.now(UTC)
            await session.commit()

        email2_hash = hashlib.sha256(email2.lower().strip().encode("utf-8")).hexdigest()
        await redis.delete(f"rl:pwd_reset:cooldown:{email2_hash}")

        resp_deleted = await client.post(
            "/api/v1/identity/forgot-password",
            json={"email": email2},
            headers={"x-forwarded-for": "10.0.0.4"},
        )
        assert resp_deleted.status_code == 200
        assert (
            resp_deleted.json()["data"]["message"]
            == "If an account exists for that email address, we've sent password reset instructions."
        )
        assert len(queued_resets) == 0


@pytest.mark.asyncio
async def test_password_reset_invalid_token_and_weak_password():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid / non-existent token on verify
        verify_resp = await client.get(
            "/api/v1/identity/reset-password/verify?token=completely_fake_invalid_token_123",
            headers={"x-forwarded-for": "10.0.0.5"},
        )
        assert verify_resp.status_code == 422
        assert "invalid or has expired" in verify_resp.json()["error"]["message"]

        # Invalid token on reset
        reset_resp = await client.post(
            "/api/v1/identity/reset-password",
            json={
                "token": "completely_fake_invalid_token_123",
                "new_password": "ValidPassword123!",
            },
            headers={"x-forwarded-for": "10.0.0.5"},
        )
        assert reset_resp.status_code == 422
        assert "invalid or has expired" in reset_resp.json()["error"]["message"]

        # Weak password (< 8 characters)
        weak_resp = await client.post(
            "/api/v1/identity/reset-password",
            json={"token": "some_token_here", "new_password": "short"},
            headers={"x-forwarded-for": "10.0.0.5"},
        )
        assert weak_resp.status_code == 422


@pytest.mark.asyncio
async def test_password_reset_rate_limiting():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        ip = "192.168.100.99"
        # Hit IP rate limit (max 10 requests / 15 min)
        last_resp = None
        for i in range(12):
            resp = await client.post(
                "/api/v1/identity/forgot-password",
                json={"email": f"rate.limit.test.{i}@olivet-test.edu.ng"},
                headers={"x-forwarded-for": ip},
            )
            last_resp = resp
            if resp.status_code == 429:
                break

        assert last_resp is not None
        assert last_resp.status_code == 429
        assert last_resp.json()["error"]["code"] == "RATE_LIMITED"
        assert "Too many password reset requests" in last_resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_request_password_reset_aborts_email_on_redis_failure(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.modules.identity import services as identity_services

    queued_resets: list[tuple] = []

    class FakeResetEmailTask:
        def delay(self, *args, **kwargs):
            queued_resets.append(args)

    monkeypatch.setattr(identity_tasks, "send_password_reset_email", FakeResetEmailTask())

    class BrokenRedis:
        async def incr(self, *args, **kwargs):
            return 1

        async def expire(self, *args, **kwargs):
            return True

        async def set(self, key, *args, **kwargs):
            if str(key).startswith("pwd_reset:"):
                raise ConnectionError("Redis unavailable")
            return True

        async def get(self, *args, **kwargs):
            return None

    monkeypatch.setattr(identity_services, "get_redis", lambda: BrokenRedis())

    fake_user = SimpleNamespace(
        id=uuid.uuid4(),
        email="teacher@olivet-test.edu.ng",
        first_name="Funke",
        is_active=True,
        deleted_at=None,
    )
    fake_result = SimpleNamespace(scalar_one_or_none=lambda: fake_user)
    fake_db = SimpleNamespace(execute=AsyncMock(return_value=fake_result))

    await identity_services.request_password_reset(fake_db, fake_user.email, "10.0.0.6")
    assert queued_resets == []
