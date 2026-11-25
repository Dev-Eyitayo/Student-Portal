import re
from typing import Any

# Matches Nigerian mobile numbers:
# - Local: 070..., 080..., 081..., 090..., 091... (11 digits)
# - International: +23470..., 23470..., +23480..., +23481..., +23490..., +23491...
NIGERIAN_PHONE_PATTERN = re.compile(r"^(?:\+?234|0)[789][01]\d{8}$")
_SEPARATOR_PATTERN = re.compile(r"[\s\-()]+")

_NIGERIAN_PHONE_ERROR = (
    "Please enter a valid Nigerian phone number (for example, 08031234567 or +2348031234567)."
)


def validate_nigerian_phone(value: Any) -> str:
    """Validate a required Nigerian phone number, stripping formatting separators."""
    if value is None:
        raise ValueError(_NIGERIAN_PHONE_ERROR)
    cleaned = _SEPARATOR_PATTERN.sub("", str(value).strip())
    if not NIGERIAN_PHONE_PATTERN.match(cleaned):
        raise ValueError(_NIGERIAN_PHONE_ERROR)
    return cleaned


def validate_optional_nigerian_phone(value: Any) -> str | None:
    """Validate an optional Nigerian phone number, treating blank strings as None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return validate_nigerian_phone(text)
