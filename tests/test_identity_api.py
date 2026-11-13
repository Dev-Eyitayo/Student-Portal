import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.core.database import async_session_factory
from app.modules.identity.seed import seed_system_permissions


@pytest.fixture(autouse=True)
async def seed_permissions_fixture():
    async with async_session_factory() as session:
        await seed_system_permissions(session)


@pytest.mark.asyncio
async def test_full_identity_and_onboarding_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unique_id = uuid.uuid4().hex[:6]
        # 1. School Onboarding (Tenant registration + Initial School Admin)
        school_payload = {
            "name": f"Olivet Baptist High School {unique_id}",
            "slug": f"olivet-{unique_id}",
            "official_code": f"OBHS-{unique_id.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"principal.{unique_id}@olivet-test.edu.ng",
            "admin_first_name": "Adeyemi",
            "admin_last_name": "Olufemi",
            "admin_password": "SuperAdminSecurePassword123!",
        }
        res_school = await client.post("/api/v1/identity/schools", json=school_payload)
        assert res_school.status_code == 201, res_school.text
        school_data = res_school.json()
        assert school_data["slug"] == f"olivet-{unique_id}"
        school_id = school_data["id"]

        # 2. Login as School Admin
        login_payload = {
            "email": f"principal.{unique_id}@olivet-test.edu.ng",
            "password": "SuperAdminSecurePassword123!",
        }
        res_login = await client.post("/api/v1/identity/login", json=login_payload)
        assert res_login.status_code == 200, res_login.text
        tokens = res_login.json()
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        admin_access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        admin_headers = {"Authorization": f"Bearer {admin_access_token}"}

        # 3. Test Invalid Password Login (Empathetic error)
        bad_login = await client.post("/api/v1/identity/login", json={
            "email": f"principal.{unique_id}@olivet-test.edu.ng",
            "password": "WrongPassword!",
        })
        assert bad_login.status_code == 401
        assert "incorrect" in bad_login.json()["detail"].lower()

        # 4. GET /identity/me
        res_me = await client.get("/api/v1/identity/me", headers=admin_headers)
        assert res_me.status_code == 200, res_me.text
        me_data = res_me.json()
        assert me_data["email"] == f"principal.{unique_id}@olivet-test.edu.ng"
        assert me_data["role"] == "SCHOOL_ADMIN"
        assert "Olivet Baptist High School" in me_data["school_name"]
        assert "scores:approve" in me_data["permissions"]

        # 5. Token Refresh
        res_refresh = await client.post("/api/v1/identity/refresh", json={
            "refresh_token": refresh_token
        })
        assert res_refresh.status_code == 200, res_refresh.text
        refreshed_tokens = res_refresh.json()
        assert "access_token" in refreshed_tokens

        # 6. Admin invites a Teacher
        invite_payload = {
            "email": f"teacher.{unique_id}@olivet-test.edu.ng",
            "first_name": "Funke",
            "last_name": "Adebayo",
            "phone_number": "+2348031234567",
            "role": "TEACHER",
            "staff_id_number": f"STF-{unique_id.upper()}",
            "specialization": "Chemistry & Biology",
        }
        res_invite = await client.post("/api/v1/identity/staff/invite", json=invite_payload, headers=admin_headers)
        assert res_invite.status_code == 201, res_invite.text
        invite_data = res_invite.json()
        assert "invitation_token" in invite_data
        invitation_token = invite_data["invitation_token"]

        # 7. Teacher verifies invitation token
        res_verify = await client.get(f"/api/v1/identity/staff/verify-invite?token={invitation_token}")
        assert res_verify.status_code == 200, res_verify.text
        verify_data = res_verify.json()
        assert verify_data["email"] == f"teacher.{unique_id}@olivet-test.edu.ng"
        assert verify_data["first_name"] == "Funke"
        assert "Olivet Baptist High School" in verify_data["school_name"]

        # 8. Teacher accepts invitation and sets password
        accept_payload = {
            "invitation_token": invitation_token,
            "password": "TeacherStrongPassword2026!",
            "confirm_password": "TeacherStrongPassword2026!",
        }
        res_accept = await client.post("/api/v1/identity/staff/accept-invitation", json=accept_payload)
        assert res_accept.status_code == 200, res_accept.text
        teacher_session = res_accept.json()
        teacher_access_token = teacher_session["access_token"]
        teacher_refresh_token = teacher_session["refresh_token"]
        teacher_headers = {"Authorization": f"Bearer {teacher_access_token}"}

        # 9. Teacher checks their profile
        res_teacher_me = await client.get("/api/v1/identity/me", headers=teacher_headers)
        assert res_teacher_me.status_code == 200
        teacher_me = res_teacher_me.json()
        assert teacher_me["role"] == "TEACHER"
        assert teacher_me["is_active"] is True
        teacher_user_id = teacher_me["id"]

        # 10. Admin delegates scoped permission (e.g. scores:approve for Science)
        grant_payload = {
            "permission_code": "scores:approve",
            "scope_type": "SCHOOL_WIDE",
        }
        res_grant = await client.post(
            f"/api/v1/identity/users/{teacher_user_id}/permissions",
            json=grant_payload,
            headers=admin_headers,
        )
        assert res_grant.status_code == 201, res_grant.text

        # Teacher now has scores:approve in their effective permissions
        res_teacher_updated_me = await client.get("/api/v1/identity/me", headers=teacher_headers)
        assert "scores:approve" in res_teacher_updated_me.json()["permissions"]

        # 11. Teacher logs out (Revokes access token jti via Redis)
        res_logout = await client.post("/api/v1/identity/logout", headers=teacher_headers)
        assert res_logout.status_code == 200
        assert "successfully signed out" in res_logout.json()["message"]

        # Calling /me with the logged out token must fail with 401
        res_after_logout = await client.get("/api/v1/identity/me", headers=teacher_headers)
        assert res_after_logout.status_code == 401

        # The refresh token is revoked as well and can no longer mint a session
        res_refresh_after_logout = await client.post(
            "/api/v1/identity/refresh",
            json={"refresh_token": teacher_refresh_token},
        )
        assert res_refresh_after_logout.status_code == 401

        # 12. Admin deactivates teacher
        res_deactivate = await client.post(
            f"/api/v1/identity/users/{teacher_user_id}/deactivate",
            headers=admin_headers,
        )
        assert res_deactivate.status_code == 200
        assert res_deactivate.json()["is_active"] is False

        # Attempting to login as deactivated teacher must fail with 403
        res_inactive_login = await client.post("/api/v1/identity/login", json={
            "email": f"teacher.{unique_id}@olivet-test.edu.ng",
            "password": "TeacherStrongPassword2026!",
        })
        assert res_inactive_login.status_code == 403
        assert "inactive" in res_inactive_login.json()["detail"].lower()

