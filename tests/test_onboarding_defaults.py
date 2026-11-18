import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.storage import StorageService
from app.main import app
from app.modules.academic_structure.templates import get_structure_template_preview
from app.modules.identity.schemas import AssessmentWeightSchema, GradingScaleSchema


@pytest.mark.asyncio
async def test_onboarding_templates_are_public():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/identity/onboarding/templates")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["data"]) == 1
    template = body["data"][0]
    assert template["id"] == "olivet_nigeria_default"
    assert template["configuration_defaults"]["timezone"] == "Africa/Lagos"
    assert template["configuration_defaults"]["currency"] == "NGN"
    assert (
        template["configuration_defaults"]["assessment_weight_schema"]["EXAM"]["weight_percentage"]
        == 70
    )
    assert template["structure_template"]["class_group_count"] == 120
    assert template["custom_domain"]["status"] == "PLANNED_POST_PILOT"


def test_structure_template_preview_is_deterministic():
    preview = get_structure_template_preview("olivet_nigeria_jss_ss3")

    assert preview is not None
    assert len(preview["levels"]) == 6
    assert len(preview["departments"]) == 3
    assert len(preview["arms"]) == 10
    assert preview["class_groups"][0]["level_code"] == "JSS1"
    assert preview["class_groups"][0]["department_code"] is None


def test_grading_scale_rejects_gaps():
    with pytest.raises(ValidationError):
        GradingScaleSchema.model_validate(
            {
                "grades": [
                    {
                        "grade": "F9",
                        "min_score": 0,
                        "max_score": 39,
                        "grade_point": 0,
                        "remark": "Fail",
                    },
                    {
                        "grade": "A1",
                        "min_score": 40,
                        "max_score": 100,
                        "grade_point": 4,
                        "remark": "Excellent",
                    },
                ]
            }
        )


def test_assessment_weights_must_total_one_hundred():
    with pytest.raises(ValidationError):
        AssessmentWeightSchema.model_validate(
            {
                "components": {
                    "TEST1": {"name": "Test 1", "max_score": 10, "weight_percentage": 10},
                    "EXAM": {"name": "Exam", "max_score": 70, "weight_percentage": 80},
                }
            }
        )


@pytest.mark.asyncio
async def test_branding_assets_are_isolated_by_school(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        STORAGE_LOCAL_DIR=str(tmp_path),
        STORAGE_PUBLIC_BASE_URL="https://files.example.test",
    )
    service = StorageService(settings)

    first_path = await service.save_school_branding_asset(
        school_slug="first-school",
        asset_type="logo",
        content_type="image/png",
        content=b"first-image",
    )
    second_path = await service.save_school_branding_asset(
        school_slug="second-school",
        asset_type="logo",
        content_type="image/png",
        content=b"second-image",
    )

    assert first_path.startswith("schools/first-school/branding/logo/")
    assert second_path.startswith("schools/second-school/branding/logo/")
    assert (tmp_path / first_path).read_bytes() == b"first-image"
    assert (tmp_path / second_path).read_bytes() == b"second-image"
    assert service.public_url(first_path) == f"https://files.example.test/{first_path}"


@pytest.mark.asyncio
async def test_apply_structure_template_on_brand_new_school_creates_full_class_groups():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Olivet Onboarding School {suffix}",
            "slug": f"olivet-onboarding-{suffix}",
            "official_code": f"OBHS-ONB-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"onboarding.principal.{suffix}@olivet-test.edu.ng",
            "admin_first_name": "Adeyemi",
            "admin_last_name": "Olufemi",
            "admin_password": "SuperAdminSecurePassword123!",
        }
        register_resp = await client.post("/api/v1/identity/schools", json=payload)
        assert register_resp.status_code == 201, register_resp.text

        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": payload["admin_email"], "password": payload["admin_password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        # Regression: on a brand-new school with no existing levels/departments/arms,
        # the newly created rows must be flushed before their ids are read to build
        # ClassGroup rows -- otherwise every ClassGroup is inserted with a NULL
        # academic_level_id/class_arm_id and the whole template apply fails.
        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "olivet_nigeria_jss_ss3",
                "session_name": f"2024/2025 {suffix}",
                "session_start_date": "2024-09-09",
                "session_end_date": "2025-07-18",
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        data = apply_resp.json()["data"]
        assert data["levels_created"] == 6
        assert data["departments_created"] == 3
        assert data["arms_created"] == 10
        assert data["class_groups_created"] == 120
        assert data["class_groups_reused"] == 0

        groups_resp = await client.get(
            "/api/v1/structure/class-groups",
            params={"session_id": data["academic_session_id"], "page_size": 200},
            headers=headers,
        )
        assert groups_resp.status_code == 200, groups_resp.text
        items = groups_resp.json()["data"]["items"]
        assert len(items) == 120
        assert all(item["academic_level_id"] is not None for item in items)
        assert all(item["class_arm_id"] is not None for item in items)

        # Re-applying is idempotent: nothing new is created the second time.
        reapply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "olivet_nigeria_jss_ss3",
                "academic_session_id": data["academic_session_id"],
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert reapply_resp.status_code == 201, reapply_resp.text
        reapply_data = reapply_resp.json()["data"]
        assert reapply_data["levels_created"] == 0
        assert reapply_data["departments_created"] == 0
        assert reapply_data["arms_created"] == 0
        assert reapply_data["class_groups_created"] == 0
        assert reapply_data["class_groups_reused"] == 120
