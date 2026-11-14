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
    payload = {
        "sub": "018f2195-2c8b-7000-8542-0123456789ab",
        "role": "TEACHER",
        "school_id": "018f2195-2c8b-7000-8542-abcdefabcdef",
    }
    token = create_access_token(payload)
    assert isinstance(token, str)

    decoded = decode_access_token(token)
    assert decoded["sub"] == payload["sub"]
    assert decoded["role"] == "TEACHER"
    assert "exp" in decoded
    assert "iat" in decoded
    assert "jti" in decoded
    assert len(decoded["jti"]) > 10


@pytest.mark.asyncio
async def test_token_jti_revocation():
    from app.core.security import is_token_revoked, revoke_access_token

    payload = {"sub": "018f2195-2c8b-7000-8542-0123456789ab", "role": "TEACHER"}
    token = create_access_token(payload)
    decoded = decode_access_token(token)
    jti = decoded["jti"]

    # Initially not revoked
    assert await is_token_revoked(jti) is False

    # Revoke it
    await revoke_access_token(jti, expires_in_seconds=60)

    # Now it is instantly revoked
    assert await is_token_revoked(jti) is True
