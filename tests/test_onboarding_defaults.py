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
    assert len(body["data"]) == 5
    secondary = next(item for item in body["data"] if item["id"] == "olivet_nigeria_default")
    primary = next(item for item in body["data"] if item["id"] == "nigeria_primary_default")
    combined = next(item for item in body["data"] if item["id"] == "nigeria_combined_default")
    assert combined["school_type"] == "COMBINED"

    assert secondary["school_type"] == "SECONDARY"
    assert secondary["configuration_defaults"]["timezone"] == "Africa/Lagos"
    assert secondary["configuration_defaults"]["currency"] == "NGN"
    assert (
        secondary["configuration_defaults"]["assessment_weight_schema"]["EXAM"]["weight_percentage"]
        == 70
    )
    assert secondary["structure_template"]["arms"] == ["A", "B", "C"]
    assert secondary["structure_template"]["class_group_count"] == 36

    assert primary["school_type"] == "PRIMARY"
    assert primary["configuration_defaults"]["report_card_template_id"] == "default_primary_v1"
    assert primary["configuration_defaults"]["grading_scale_schema"]["grades"][0]["grade"] == "A"
    assert primary["structure_template"]["class_group_count"] == 18
    assert all(item["custom_domain"]["status"] == "PLANNED_POST_PILOT" for item in body["data"])


def test_structure_template_preview_is_deterministic():
    preview = get_structure_template_preview("olivet_nigeria_jss_ss3")

    assert preview is not None
    assert len(preview["levels"]) == 6
    assert len(preview["departments"]) == 3
    assert len(preview["arms"]) == 10
    assert preview["class_groups"][0]["level_code"] == "JSS1"
    assert preview["class_groups"][0]["department_code"] is None


def test_primary_structure_template_preview_is_deterministic():
    preview = get_structure_template_preview("nigeria_primary_basic1_6")

    assert preview is not None
    assert preview["template_type"] == "PRIMARY"
    assert len(preview["levels"]) == 6
    assert preview["levels"][0]["code"] == "BASIC1"
    assert len(preview["departments"]) == 0
    assert preview["arms"] == ["A", "B", "C"]
    assert len(preview["subjects"]) == 13
    assert preview["class_group_count"] == 18
    assert all(item["department_code"] is None for item in preview["class_groups"])


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


def test_assessment_weights_accept_stored_flat_shape():
    schema = AssessmentWeightSchema.model_validate(
        {
            "TEST1": {"name": "Test 1", "max_score": 10, "weight_percentage": 10},
            "EXAM": {"name": "Exam", "max_score": 90, "weight_percentage": 90},
        }
    )

    assert schema.components["EXAM"].max_score == 90


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


@pytest.mark.asyncio
async def test_primary_school_registration_seeds_primary_defaults():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Olivet Primary Onboarding {suffix}",
            "slug": f"olivet-primary-{suffix}",
            "official_code": f"OBHS-PRI-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"primary.principal.{suffix}@olivet-test.edu.ng",
            "admin_first_name": "Adebayo",
            "admin_last_name": "Adeyemi",
            "admin_password": "PrimaryAdminSecurePassword123!",
            "school_type": "PRIMARY",
        }
        register_resp = await client.post("/api/v1/identity/schools", json=payload)
        assert register_resp.status_code == 201, register_resp.text
        school_id = register_resp.json()["data"]["id"]

        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": payload["admin_email"], "password": payload["admin_password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        setup_resp = await client.get(
            f"/api/v1/identity/schools/{school_id}/setup", headers=headers
        )
        assert setup_resp.status_code == 200, setup_resp.text
        data = setup_resp.json()["data"]

        assert data["school"]["school_type"] == "PRIMARY"
        config = data["configuration"]
        assert config["report_card_template_id"] == "default_primary_v1"
        assert {grade["grade"] for grade in config["grading_scale_schema"]["grades"]} == {
            "A",
            "B",
            "C",
            "D",
            "E",
            "F",
        }
        assert all(grade["grade_point"] == 0 for grade in config["grading_scale_schema"]["grades"])
        assert config["assessment_weight_schema"]["TEST1"]["weight_percentage"] == 10
        assert config["assessment_weight_schema"]["ASSIGN"]["weight_percentage"] == 10
        assert config["assessment_weight_schema"]["EXAM"]["weight_percentage"] == 70


@pytest.mark.asyncio
async def test_apply_primary_template_creates_core_subjects_idempotently():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Olivet Primary Template {suffix}",
            "slug": f"olivet-primary-template-{suffix}",
            "official_code": f"OBHS-TPL-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"primary.template.{suffix}@olivet-test.edu.ng",
            "admin_first_name": "Adebayo",
            "admin_last_name": "Adeyemi",
            "admin_password": "PrimaryTemplateSecurePassword123!",
            "school_type": "PRIMARY",
        }
        register_resp = await client.post("/api/v1/identity/schools", json=payload)
        assert register_resp.status_code == 201, register_resp.text

        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": payload["admin_email"], "password": payload["admin_password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
                "session_name": f"2024/2025 {suffix}",
                "session_start_date": "2024-09-09",
                "session_end_date": "2025-07-18",
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        data = apply_resp.json()["data"]
        assert data["template_id"] == "nigeria_primary_basic1_6"
        assert data["template_type"] == "PRIMARY"
        assert data["levels_created"] == 6
        assert data["departments_created"] == 0
        assert data["arms_created"] == 3
        assert data["class_groups_created"] == 18
        assert data["subjects_created"] == 13
        assert data["subject_offerings_created"] == 78
        assert data["class_subjects_created"] == 234

        subjects_resp = await client.get(
            "/api/v1/subjects", params={"page_size": 100}, headers=headers
        )
        assert subjects_resp.status_code == 200, subjects_resp.text
        subjects = subjects_resp.json()["data"]["items"]
        assert len(subjects) == 13
        assert any(subject["is_elective"] is False for subject in subjects)
        assert all(subject["is_trade_subject"] is False for subject in subjects)

        reapply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
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
        assert reapply_data["subjects_created"] == 0
        assert reapply_data["subject_offerings_created"] == 0
        assert reapply_data["class_subjects_created"] == 0


@pytest.mark.asyncio
async def test_staging_local_storage_returns_stub_without_writing_to_disk(tmp_path: Path):
    staging_settings = Settings(
        ENVIRONMENT="staging",
        STORAGE_PROVIDER="local",
        STORAGE_LOCAL_DIR=str(tmp_path),
    )
    service = StorageService(staging_settings)

    path = await service.save_school_branding_asset(
        school_slug="olivet-staging",
        asset_type="logo",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfake-png-bytes",
    )

    assert path.startswith("schools/olivet-staging/branding/logo/")
    assert path.endswith(".png")
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_session_rollover_is_idempotent_and_easy_to_read():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        school_payload = {
            "name": f"Rollover Primary {suffix}",
            "slug": f"rollover-primary-{suffix}",
            "official_code": f"RLV-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"admin-{suffix}@example.edu.ng",
            "admin_first_name": "Ada",
            "admin_last_name": "Obi",
            "admin_password": "StrongPassword123!",
            "school_type": "PRIMARY",
        }
        reg_resp = await client.post("/api/v1/identity/schools", json=school_payload)
        assert reg_resp.status_code == 201, reg_resp.text
        login_resp = await client.post(
            "/api/v1/identity/login",
            json={
                "email": school_payload["admin_email"],
                "password": school_payload["admin_password"],
            },
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_type": "PRIMARY",
                "session_name": f"2025/2026 {suffix}",
                "session_start_date": "2025-09-08",
                "session_end_date": "2026-07-24",
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        source_session_id = apply_resp.json()["data"]["academic_session_id"]

        new_session_resp = await client.post(
            "/api/v1/structure/sessions",
            json={
                "name": f"2026/2027 {suffix}",
                "start_date": "2026-09-07",
                "end_date": "2027-07-23",
                "is_current": True,
            },
            headers=headers,
        )
        assert new_session_resp.status_code == 201, new_session_resp.text
        target_session_id = new_session_resp.json()["data"]["id"]

        rollover_resp = await client.post(
            f"/api/v1/structure/sessions/{target_session_id}/rollover",
            json={
                "source_session_id": source_session_id,
                "copy_class_groups": True,
                "copy_class_teachers": True,
                "copy_subject_offerings": True,
                "copy_class_subjects": True,
                "copy_subject_teachers": True,
            },
            headers=headers,
        )
        assert rollover_resp.status_code == 200, rollover_resp.text
        rdata = rollover_resp.json()["data"]
        assert rdata["class_groups_created"] == 18
        assert rdata["subject_offerings_created"] == 78
        assert rdata["class_subjects_created"] == 234
        assert f"2026/2027 {suffix} is now set up from 2025/2026 {suffix}" in rdata["message"]

        rerun_resp = await client.post(
            f"/api/v1/structure/sessions/{target_session_id}/rollover",
            json={"source_session_id": source_session_id},
            headers=headers,
        )
        assert rerun_resp.status_code == 200, rerun_resp.text
        rerun = rerun_resp.json()["data"]
        assert rerun["class_groups_created"] == 0
        assert rerun["class_groups_reused"] == 18
        assert rerun["subject_offerings_created"] == 0
        assert rerun["subject_offerings_reused"] == 78
        assert rerun["class_subjects_created"] == 0
        assert rerun["class_subjects_reused"] == 234
        assert "already has all matching classes and subjects" in rerun["message"]

        other_suffix = uuid.uuid4().hex[:6]
        other_payload = {
            "name": f"Other School {other_suffix}",
            "slug": f"other-school-{other_suffix}",
            "official_code": f"OTH-{other_suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "Olivet Heights, Oyo, Nigeria",
            "country_code": "NG",
            "admin_email": f"other-{other_suffix}@example.edu.ng",
            "admin_first_name": "Tunde",
            "admin_last_name": "Bello",
            "admin_password": "StrongPassword123!",
            "school_type": "PRIMARY",
        }
        other_reg = await client.post("/api/v1/identity/schools", json=other_payload)
        assert other_reg.status_code == 201, other_reg.text
        other_login = await client.post(
            "/api/v1/identity/login",
            json={
                "email": other_payload["admin_email"],
                "password": other_payload["admin_password"],
            },
        )
        assert other_login.status_code == 200, other_login.text
        other_headers = {"Authorization": f"Bearer {other_login.json()['data']['access_token']}"}
        cross_tenant_resp = await client.post(
            f"/api/v1/structure/sessions/{target_session_id}/rollover",
            json={"source_session_id": source_session_id},
            headers=other_headers,
        )
        assert cross_tenant_resp.status_code == 404


@pytest.mark.asyncio
async def test_incremental_arms_selected_subjects_and_setup_wizard_flags():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Incremental Arms High {suffix}",
            "slug": f"incremental-arms-{suffix}",
            "official_code": f"IAH-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "10 Unity Road, Oyo",
            "country_code": "NG",
            "admin_email": f"admin.{suffix}@incremental-test.edu.ng",
            "admin_first_name": "Bisi",
            "admin_last_name": "Ogunleye",
            "admin_password": "IncrementalSecurePassword123!",
            "school_type": "SECONDARY",
        }
        register_resp = await client.post("/api/v1/identity/schools", json=payload)
        assert register_resp.status_code == 201, register_resp.text
        school_id = register_resp.json()["data"]["id"]

        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": payload["admin_email"], "password": payload["admin_password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        setup_resp = await client.get(
            f"/api/v1/identity/schools/{school_id}/setup", headers=headers
        )
        assert setup_resp.status_code == 200, setup_resp.text
        config = setup_resp.json()["data"]["configuration"]
        assert config["setup_wizard_completed"] is False
        assert config["setup_wizard_dismissed"] is False

        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "nigeria_senior_secondary",
                "session_name": f"2025/2026 {suffix}",
                "session_start_date": "2025-09-08",
                "session_end_date": "2026-07-24",
                "include_class_groups": True,
                "arms_per_level": 2,
                "department_arm_counts": {"SCI": 2, "ART": 1, "COMM": 1},
                "selected_subject_codes": ["ENG", "MTH", "PHY", "CHM", "LIT", "ACC"],
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        adata = apply_resp.json()["data"]
        assert adata["arms_created"] == 2
        assert adata["class_groups_created"] == 12
        assert adata["subjects_created"] == 6

        arms_resp = await client.get("/api/v1/structure/arms", headers=headers)
        assert arms_resp.status_code == 200, arms_resp.text
        arm_letters = sorted(item["name"] for item in arms_resp.json()["data"]["items"])
        assert arm_letters == ["A", "B"]

        dismiss_resp = await client.patch(
            f"/api/v1/identity/schools/{school_id}/configuration",
            json={"setup_wizard_dismissed": True},
            headers=headers,
        )
        assert dismiss_resp.status_code == 200, dismiss_resp.text
        assert dismiss_resp.json()["data"]["setup_wizard_dismissed"] is True
        assert dismiss_resp.json()["data"]["setup_wizard_completed"] is False

        complete_resp = await client.patch(
            f"/api/v1/identity/schools/{school_id}/configuration",
            json={"setup_wizard_completed": True, "setup_wizard_dismissed": False},
            headers=headers,
        )
        assert complete_resp.status_code == 200, complete_resp.text
        assert complete_resp.json()["data"]["setup_wizard_completed"] is True
        assert complete_resp.json()["data"]["setup_wizard_dismissed"] is False

        locked_apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "nigeria_senior_secondary",
                "session_name": f"2026/2027 {suffix}",
                "session_start_date": "2026-09-07",
                "session_end_date": "2027-07-23",
                "include_class_groups": True,
            },
            headers=headers,
        )
        assert locked_apply_resp.status_code == 409, locked_apply_resp.text
        assert locked_apply_resp.json()["error"]["code"] == "SETUP_WIZARD_ALREADY_COMPLETED"


@pytest.mark.asyncio
async def test_mixed_senior_secondary_without_departments():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        suffix = uuid.uuid4().hex[:6]
        payload = {
            "name": f"Mixed Senior High {suffix}",
            "slug": f"mixed-senior-{suffix}",
            "official_code": f"MSH-{suffix.upper()}",
            "state": "Oyo State",
            "city": "Oyo",
            "address": "15 Mixed Class Way, Oyo",
            "country_code": "NG",
            "admin_email": f"admin.{suffix}@mixed-senior-test.edu.ng",
            "admin_first_name": "Kemi",
            "admin_last_name": "Balogun",
            "admin_password": "MixedSeniorSecurePassword123!",
            "school_type": "SECONDARY",
        }
        register_resp = await client.post("/api/v1/identity/schools", json=payload)
        assert register_resp.status_code == 201, register_resp.text

        login_resp = await client.post(
            "/api/v1/identity/login",
            json={"email": payload["admin_email"], "password": payload["admin_password"]},
        )
        assert login_resp.status_code == 200, login_resp.text
        headers = {"Authorization": f"Bearer {login_resp.json()['data']['access_token']}"}

        apply_resp = await client.post(
            "/api/v1/structure/templates/apply",
            json={
                "template_id": "nigeria_senior_secondary",
                "session_name": f"2025/2026 {suffix}",
                "session_start_date": "2025-09-08",
                "session_end_date": "2026-07-24",
                "include_class_groups": True,
                "separate_senior_departments": False,
                "arms_per_level": 1,
                "level_arm_counts": {"SS1": 2, "SS2": 1, "SS3": 1},
                "selected_subject_codes": ["ENG", "PHY", "LIT"],
            },
            headers=headers,
        )
        assert apply_resp.status_code == 201, apply_resp.text
        adata = apply_resp.json()["data"]
        assert adata["departments_created"] == 0
        assert adata["arms_created"] == 2
        assert adata["class_groups_created"] == 4
        assert adata["subjects_created"] == 3

        subjects_resp = await client.get(
            "/api/v1/subjects", params={"page_size": 20}, headers=headers
        )
        assert subjects_resp.status_code == 200, subjects_resp.text
        by_code = {s["code"]: s for s in subjects_resp.json()["data"]["items"]}
        assert by_code["ENG"]["is_elective"] is False
        assert by_code["PHY"]["is_elective"] is True
        assert by_code["LIT"]["is_elective"] is True
