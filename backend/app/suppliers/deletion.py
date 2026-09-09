"""供应商删除保护：影响统计、受保护删除与审计。

- 统计把全部 SupplierEventMatch 计入历史（不只 current），alert_count 覆盖全部状态；
- DELETE 先以 SELECT ... FOR UPDATE 锁定父行，再复查 match 引用；
- 有历史时：拒绝审计先独立提交（409 不能回滚审计），业务数据不变；
- 无历史时：删除与 supplier_delete 成功审计同事务提交，resource_id 为字符串，
  不依赖被删供应商的外键。
"""
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import write_audit
from app.risks.models import RiskAlert, SupplierEventMatch
from app.suppliers.models import Supplier
from app.suppliers.queries import supplier_options
from app.suppliers.schemas import DeletionImpact

BLOCKED_REASON = "supplier_has_risk_history"
BLOCKED_MESSAGE = "供应商存在风险关联历史，已阻止删除；可暂停监控以保留历史。"


def lock_supplier_for_delete(session: Session, supplier_id: int) -> Supplier | None:
    """锁定供应商父行：阻止并发删除/引用穿透（FK KEY SHARE 与 UPDATE 锁冲突）。"""
    return session.scalar(
        select(Supplier)
        .where(Supplier.id == supplier_id)
        .options(*supplier_options())
        .with_for_update()
    )


def count_supplier_history(session: Session, supplier_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(SupplierEventMatch)
            .where(SupplierEventMatch.supplier_id == supplier_id)
        )
        or 0
    )


def count_supplier_alerts(session: Session, supplier_id: int) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(RiskAlert)
            .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
            .where(SupplierEventMatch.supplier_id == supplier_id)
        )
        or 0
    )


def build_deletion_impact(session: Session, supplier: Supplier) -> DeletionImpact:
    match_count = count_supplier_history(session, supplier.id)
    can_delete = match_count == 0
    return DeletionImpact(
        can_delete=can_delete,
        match_count=match_count,
        alert_count=count_supplier_alerts(session, supplier.id),
        sites_count=len(supplier.sites),
        products_count=len(supplier.products),
        aliases_count=len(supplier.aliases),
        blocked_reason=None if can_delete else BLOCKED_REASON,
    )


def delete_supplier_guarded(
    session: Session, supplier_id: int, *, actor_user_id: int | None
) -> None:
    """原子删除：锁定父行后复查全部 match 引用，按结果写审计并提交。"""
    supplier = lock_supplier_for_delete(session, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="供应商不存在")

    match_count = count_supplier_history(session, supplier_id)
    if match_count > 0:
        write_audit(
            session,
            action="supplier_delete_rejected",
            actor_user_id=actor_user_id,
            resource_type="supplier",
            resource_id=str(supplier_id),
            success=False,
            detail=f"blocked_reason={BLOCKED_REASON};match_count={match_count}",
        )
        # 拒绝审计先独立提交：随后的 409 不得回滚审计；本事务未修改业务数据。
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": BLOCKED_REASON, "message": BLOCKED_MESSAGE},
        )

    session.delete(supplier)
    write_audit(
        session,
        action="supplier_delete",
        actor_user_id=actor_user_id,
        resource_type="supplier",
        resource_id=str(supplier_id),
        success=True,
        detail=f"supplier_code={supplier.supplier_code}",
    )
    # 删除与成功审计同事务提交：commit 失败则两者一起回滚，不伪装成功。
    session.commit()
