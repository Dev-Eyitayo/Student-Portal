import pytest
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hashing():
    raw = "MySuperSecret123!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_jwt_token_flow():
    payload = {"sub": "018f2195-2c8b-7000-8542-0123456789ab", "role": "TEACHER", "school_id": "018f2195-2c8b-7000-8542-abcdefabcdef"}
    token = create_access_token(payload)
    assert isinstance(token, str)

    decoded = decode_access_token(token)
    assert decoded["sub"] == payload["sub"]
    assert decoded["role"] == "TEACHER"
    assert "exp" in decoded
    assert "iat" in decoded
