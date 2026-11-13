"""Central SQLAlchemy model registry.

Importing this module guarantees that every mapped class and tenant table is
registered on ``Base.metadata`` before any query, mapper configuration, or
schema generation runs. Application startup, Alembic, and the test harness
must import this module first.
"""
from app.modules.common.models import Base  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.academic_structure import models as academic_structure_models  # noqa: F401
from app.modules.students import models as student_models  # noqa: F401
from app.modules.subjects import models as subject_models  # noqa: F401
from app.modules.assessments import models as assessment_models  # noqa: F401
from app.modules.computation import models as computation_models  # noqa: F401
from app.modules.distribution import models as distribution_models  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401

__all__ = ["Base"]
