import pytest

from app.core.rls import RLSEnforcementError, verify_rls_enforcement


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


class _FakeSession:
    def __init__(self, row):
        self._row = row

    async def execute(self, statement):
        return _FakeResult(self._row)


@pytest.mark.asyncio
async def test_rls_guard_rejects_superuser():
    with pytest.raises(RLSEnforcementError):
        await verify_rls_enforcement(_FakeSession((True, False)))


@pytest.mark.asyncio
async def test_rls_guard_rejects_bypassrls():
    with pytest.raises(RLSEnforcementError):
        await verify_rls_enforcement(_FakeSession((False, True)))


@pytest.mark.asyncio
async def test_rls_guard_rejects_unknown_role():
    with pytest.raises(RLSEnforcementError):
        await verify_rls_enforcement(_FakeSession(None))


@pytest.mark.asyncio
async def test_rls_guard_accepts_least_privilege_role():
    await verify_rls_enforcement(_FakeSession((False, False)))
