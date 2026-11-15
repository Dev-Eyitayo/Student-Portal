import os
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_uuid7_lock = threading.Lock()
_uuid7_last_ms = -1
_uuid7_counter = 0


def generate_uuid7() -> UUID:
    """
    Generates a time-ordered UUIDv7 (RFC 9562 draft).
    Uses 48-bit millisecond unix timestamp in the high bits,
    version 7 in bits 48-51, variant in bits 64-65, and random low bits.
    """
    global _uuid7_last_ms, _uuid7_counter

    with _uuid7_lock:
        unix_ms = time.time_ns() // 1_000_000
        if unix_ms > _uuid7_last_ms:
            _uuid7_last_ms = unix_ms
            _uuid7_counter = int.from_bytes(os.urandom(2), "big") & 0x0FFF
        else:
            unix_ms = _uuid7_last_ms
            _uuid7_counter = (_uuid7_counter + 1) & 0x0FFF
            if _uuid7_counter == 0:
                unix_ms += 1
                _uuid7_last_ms = unix_ms

        rand_bytes = os.urandom(8)

        # 48 bits timestamp
        b0 = (unix_ms >> 40) & 0xFF
        b1 = (unix_ms >> 32) & 0xFF
        b2 = (unix_ms >> 24) & 0xFF
        b3 = (unix_ms >> 16) & 0xFF
        b4 = (unix_ms >> 8) & 0xFF
        b5 = unix_ms & 0xFF

        # 4 bits version 0b0111 (7) + 12 bits monotonic counter
        b6 = 0x70 | ((_uuid7_counter >> 8) & 0x0F)
        b7 = _uuid7_counter & 0xFF

        # 2 bits variant 0b10 + 6 bits random
        b8 = 0x80 | (rand_bytes[0] & 0x3F)
        b9 = rand_bytes[1]
        b10 = rand_bytes[2]
        b11 = rand_bytes[3]
        b12 = rand_bytes[4]
        b13 = rand_bytes[5]
        b14 = rand_bytes[6]
        b15 = rand_bytes[7]

        raw_bytes = bytes([b0, b1, b2, b3, b4, b5, b6, b7, b8, b9, b10, b11, b12, b13, b14, b15])
        return UUID(bytes=raw_bytes)


class Base(DeclarativeBase):
    pass


class TenantMixin:
    """Enforces tenant foreign key on all tenant-scoped database tables."""

    school_id: Mapped[UUID] = mapped_column(
        ForeignKey("schools.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )


class TimestampMixin:
    """Standard audit timestamps with soft-delete support."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("clock_timestamp()"),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
