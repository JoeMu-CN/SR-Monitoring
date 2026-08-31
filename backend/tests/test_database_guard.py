import os
import subprocess
import sys

from pytest import ExitCode, raises
from test_stack_guard import UnsafeTestDatabaseError, require_test_database_url


def test_guard_accepts_explicit_test_database_name() -> None:
    # Given
    database_url = "postgresql+psycopg://test_user:test_password@postgres-test/supplier_risk_test"

    # When
    require_test_database_url(database_url)

    # Then: no exception means the explicitly isolated database is accepted.


def test_guard_rejects_business_database_before_continuation() -> None:
    # Given
    continuation_reached = False
    database_url = "postgresql+psycopg://test_user:test_password@postgres/supplier_risk"

    # When
    with raises(UnsafeTestDatabaseError):
        require_test_database_url(database_url)
        continuation_reached = True

    # Then
    assert continuation_reached is False


def test_guard_rejects_missing_database_name() -> None:
    # Given
    database_url = "postgresql+psycopg://test_user:test_password@postgres"

    # When / Then
    with raises(UnsafeTestDatabaseError):
        require_test_database_url(database_url)


def test_guard_rejects_unapproved_test_database_name() -> None:
    # Given
    database_url = "postgresql+psycopg://test_user:test_password@postgres/other_test"

    # When / Then
    with raises(UnsafeTestDatabaseError):
        require_test_database_url(database_url)


def test_pytest_session_rejects_business_database_before_fixture_cleanup() -> None:
    # Given
    environment = os.environ | {
        "DATABASE_URL": "postgresql+psycopg://probe:probe@invalid:5432/supplier_risk"
    }

    # When
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_database_guard.py"],
        env=environment,
        check=False,
    )

    # Then
    assert result.returncode == ExitCode.USAGE_ERROR
