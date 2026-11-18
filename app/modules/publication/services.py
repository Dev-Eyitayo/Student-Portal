from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictException, ResourceNotFoundException, ValidationException
from app.modules.assessments.models import ScoreSheetStatus
from app.modules.audit.services import AuditContext, record_audit_log
from app.modules.computation import services as computation_services
from app.modules.computation.models import SubjectResult, TermReportCard
from app.modules.computation.schemas import (
    PublishTermRequest,
    PublishTermResponse,
    ResultLinkResponse,
)
from app.modules.distribution.models import ResultAccessToken
from app.modules.identity.models import SchoolConfiguration

TOKEN_TTL_DAYS = 90


async def _commit_or_conflict(
    db: AsyncSession,
    *,
    message: str,
    code: str = "PUBLICATION_CONFLICT",
) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictException(message=message, code=code) from exc


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


async def publish_term(
    db: AsyncSession,
    school_id: UUID,
    req: PublishTermRequest,
    audit: AuditContext,
) -> PublishTermResponse:
    term = await computation_services.get_term(db, school_id, req.academic_term_id)
    if term.is_locked_for_scoring:
        raise ConflictException(
            "This term is already locked for scoring. Its results have been published and cannot "
            "be published again."
        )

    config_stmt = select(SchoolConfiguration).where(SchoolConfiguration.school_id == school_id)
    config = (await db.execute(config_stmt)).scalar_one_or_none()
    if not config:
        raise ResourceNotFoundException("school configuration", school_id)

    status_stmt = select(ScoreSheetStatus).where(
        ScoreSheetStatus.school_id == school_id,
        ScoreSheetStatus.academic_term_id == term.id,
    )
    statuses = (await db.execute(status_stmt)).scalars().all()
    not_approved = [status for status in statuses if status.status != "APPROVED"]
    if not_approved or not statuses:
        raise ValidationException(
            f"Results cannot be published yet because {len(not_approved) or 1} score sheet(s) "
            "are not approved. Please approve the outstanding score sheets first."
        )

    cards_stmt = select(TermReportCard).where(
        TermReportCard.school_id == school_id,
        TermReportCard.academic_term_id == term.id,
    )
    cards = (await db.execute(cards_stmt)).scalars().all()
    if not cards:
        raise ValidationException(
            "Results cannot be published yet because no report cards have been calculated. "
            "Please calculate this term's results first."
        )
    published_cards = [card for card in cards if card.publication_status == "PUBLISHED"]
    if published_cards:
        raise ConflictException(
            "This term's report cards are already published and cannot be published again."
        )

    results_stmt = select(SubjectResult).where(
        SubjectResult.school_id == school_id,
        SubjectResult.academic_term_id == term.id,
    )
    subject_results = (await db.execute(results_stmt)).scalars().all()

    snapshot = computation_services.build_snapshot(config)
    published_at = datetime.now(UTC)
    expires_at = published_at + timedelta(days=TOKEN_TTL_DAYS)
    result_links: list[ResultLinkResponse] = []

    for result in subject_results:
        result.frozen_config_snapshot = snapshot
        result.status = "PUBLISHED"

    for card in cards:
        card.frozen_config_snapshot = snapshot
        card.publication_status = "PUBLISHED"
        card.published_at = published_at
        plain_token = token_urlsafe(32)
        token = ResultAccessToken(
            school_id=school_id,
            term_report_card_id=card.id,
            token_hash=_hash_token(plain_token),
            expires_at=expires_at,
        )
        db.add(token)
        result_links.append(
            ResultLinkResponse(
                student_enrollment_id=card.student_enrollment_id,
                token=plain_token,
                expires_at=expires_at,
            )
        )

    term.is_locked_for_scoring = True
    record_audit_log(
        db,
        audit,
        action="TERM_PUBLISHED",
        resource_type="AcademicTerm",
        resource_id=term.id,
        diff_payload={
            "after": {
                "report_cards_published": len(cards),
                "subject_results_published": len(subject_results),
                "result_tokens_generated": len(result_links),
            }
        },
    )
    await _commit_or_conflict(
        db,
        message="This term's results could not be published. Please try again.",
        code="TERM_PUBLICATION_CONFLICT",
    )
    return PublishTermResponse(
        academic_term_id=term.id,
        published_at=published_at,
        report_cards_published=len(cards),
        result_links=result_links,
    )
