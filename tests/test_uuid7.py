from uuid import UUID
from app.modules.common.models import generate_uuid7


def test_uuid7_generation():
    u = generate_uuid7()
    assert isinstance(u, UUID)
    assert u.version == 7


def test_uuid7_monotonicity():
    uuids = [generate_uuid7() for _ in range(100)]
    # All UUIDs should be unique
    assert len(set(uuids)) == 100
    # Higher index should generally have equal or greater time-ordered value
    # (Since generated in sequence)
    assert uuids[0] < uuids[-1]
