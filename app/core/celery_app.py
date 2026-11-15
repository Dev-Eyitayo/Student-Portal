from celery import Celery

from app.core.config import get_settings

settings = get_settings()

broker_url = settings.REDIS_BROKER_URL or settings.REDIS_URL

celery_app = Celery(
    "olivet",
    broker=broker_url,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.modules.identity.tasks", "app.modules.students.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_routes={
        "app.modules.identity.tasks.process_staff_bulk_import": {"queue": "bulk_imports"},
        "app.modules.identity.tasks.send_staff_invitation_email": {"queue": "notifications"},
        "app.modules.students.tasks.process_student_bulk_import": {"queue": "bulk_imports"},
    },
)

_ssl_options = {"ssl_cert_reqs": settings.REDIS_SSL_CERT_REQS}
if broker_url.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = _ssl_options
if settings.CELERY_RESULT_BACKEND and settings.CELERY_RESULT_BACKEND.startswith("rediss://"):
    celery_app.conf.redis_backend_use_ssl = _ssl_options
