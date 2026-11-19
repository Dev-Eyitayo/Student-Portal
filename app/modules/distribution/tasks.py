import logging
from html import escape
from typing import Any

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.mail import mail_client

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.modules.distribution.tasks.send_term_result_notifications",
)
def send_term_result_notifications(
    self,
    *,
    school_name: str,
    term_name: str,
    session_name: str,
    recipients: list[dict[str, str]],
) -> dict[str, Any]:
    app_url = get_settings().APP_URL.rstrip("/")
    sent = 0
    failed = 0
    for item in recipients:
        token = item["token"]
        guardian_email = item["guardian_email"]
        guardian_name = item.get("guardian_name", "")
        student_name = item.get("student_name", "your ward")
        magic_link = f"{app_url}/results/verify?token={token}"
        html = (
            f"<p>Dear {escape(guardian_name or 'Parent/Guardian')},</p>"
            f"<p>The <strong>{escape(term_name)} ({escape(session_name)})</strong> academic report for "
            f"<strong>{escape(student_name)}</strong> at <strong>{escape(school_name)}</strong> has been published.</p>"
            f'<p><a href="{escape(magic_link)}">View &amp; Verify Digital Report Card</a></p>'
            f"<p>For your child's privacy, you will be asked to confirm their date of birth or admission number.</p>"
        )
        try:
            mail_client.send_sync(
                to_email=guardian_email,
                to_name=guardian_name,
                subject=f"{school_name} — {term_name} ({session_name}) Report Card Ready",
                html=html,
            )
            sent += 1
        except Exception:
            failed += 1
            logger.exception("Failed sending result notification to %s", guardian_email)

    return {"sent": sent, "failed": failed}
