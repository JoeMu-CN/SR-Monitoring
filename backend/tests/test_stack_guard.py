from dataclasses import dataclass

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

ALLOWED_TEST_DATABASE_NAMES = frozenset({"supplier_risk_test"})


@dataclass(frozen=True, slots=True)
class UnsafeTestDatabaseError(RuntimeError):
    database_name: str | None

    def __str__(self) -> str:
        return (
            "拒绝运行数据库测试：DATABASE_URL 必须显式指向名称以 _test 结尾的测试数据库；"
            f"当前数据库为 {self.database_name or '<missing>'}"
        )


def require_test_database_url(database_url: str) -> None:
    try:
        database_name = make_url(database_url).database
    except ArgumentError as error:
        raise UnsafeTestDatabaseError(database_name=None) from error
    if (
        database_name is None
        or not database_name.endswith("_test")
        or database_name not in ALLOWED_TEST_DATABASE_NAMES
    ):
        raise UnsafeTestDatabaseError(database_name=database_name)
