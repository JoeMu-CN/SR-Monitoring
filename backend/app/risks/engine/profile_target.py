"""supplier_profile 画像事件的唯一目标定位。

画像报告（``report_kind == "supplier_profile"``）的评估对象只能是报告自身声明的
那一家供应商。本模块把三件事收敛到一个边界：

1. **解析**：校验 ``TycRiskReport`` 合同，用报告 company_name/credit_code 覆盖
   organizations（LLM 不能改目标），并返回跨周恒定的身份覆盖；
2. **定位**：以可信 ``raw_data.supplier_code`` 在**启用**供应商中唯一定位目标。
   定位不到（不存在、停用）一律明确失败，绝不回退到全清单——回退即意味着把
   一家供应商的画像按产品/地点扩散给无关供应商；
3. **收敛**：画像事件重放时，把同一事件上非目标供应商的历史 current 提醒标记为
   expired。原 level/score 与 match 行原样保留，并写入机器可读审计原因。
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agent.tyc_analysis_context import report_payload
from app.agent.tyc_report import TycRiskReport
from app.ai.schemas import OrganizationReference, SignalAnalysisResult
from app.risks.models import RiskAlert, SupplierEventMatch
from app.signals.models import RawSignal
from app.suppliers.models import Supplier

PROFILE_REPORT_KIND = "supplier_profile"

# 非目标提醒被失效时写入 score_detail 的机器可读审计原因代码。
OFF_TARGET_EXPIRY_CODE = "supplier_profile_target_only"


@dataclass(frozen=True, slots=True)
class ProfileTarget:
    """画像报告的可信目标：身份覆盖键 + 唯一定位的供应商。"""

    identity_override: str
    supplier: Supplier


def _is_profile_report(signal: RawSignal) -> bool:
    raw_data = signal.raw_data
    return isinstance(raw_data, dict) and raw_data.get("report_kind") == PROFILE_REPORT_KIND


def resolve_profile_target(
    session: Session,
    signal: RawSignal,
    result: SignalAnalysisResult,
) -> tuple[SignalAnalysisResult, ProfileTarget | None]:
    """画像报告：注入服务端主体、解析跨周恒定身份，并唯一定位目标供应商。

    非画像信号原样返回 ``(result, None)``。画像报告缺少必要字段，或 supplier_code
    在启用供应商中定位不到唯一目标时明确失败（fail closed），绝不退回普通匹配
    逻辑造成跨供应商扩散或错误合并。
    """
    if not _is_profile_report(signal):
        return result, None
    # 只剔除已知私有上下文字段：其他未知键仍由 extra="forbid" 拒绝，事件身份契约不变。
    payload = report_payload(signal.raw_data)
    try:
        report = TycRiskReport.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("supplier_profile 报告信号缺少必要字段，拒绝处理") from exc
    supplier = _load_enabled_supplier(session, report.supplier_code)
    if supplier is None:
        raise ValueError(
            f"supplier_profile 报告 supplier_code={report.supplier_code} "
            "未定位到启用中的供应商，拒绝处理"
        )
    organizations = [
        OrganizationReference(
            name=report.company_name,
            registry_no=report.credit_code or None,
        )
    ]
    return (
        result.model_copy(update={"organizations": organizations}),
        ProfileTarget(
            identity_override=f"{PROFILE_REPORT_KIND}:{report.supplier_code}",
            supplier=supplier,
        ),
    )


def _load_enabled_supplier(session: Session, supplier_code: str) -> Supplier | None:
    """按 supplier_code 取唯一启用供应商；supplier_code 已在库内有唯一约束。"""
    return session.scalar(
        select(Supplier)
        .where(Supplier.supplier_code == supplier_code, Supplier.enabled.is_(True))
        .options(
            selectinload(Supplier.aliases),
            selectinload(Supplier.sites),
            selectinload(Supplier.products),
        )
    )


def expire_off_target_alerts(
    session: Session,
    event_id: int,
    target_supplier_id: int,
    *,
    now_utc: datetime,
) -> int:
    """画像事件：把同事件上非目标供应商的 current 提醒标记为已失效。

    只改 ``status``/``updated_at`` 并追加审计原因：原 level、score 与
    ``score_detail`` 中的评分明细全部保留，match 行也不删除，历史提醒因此仍可
    追溯。legacy 提醒保持迁移前快照语义，不在此处理。
    """
    alerts = list(
        session.scalars(
            select(RiskAlert)
            .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
            .where(
                SupplierEventMatch.event_id == event_id,
                SupplierEventMatch.supplier_id != target_supplier_id,
                RiskAlert.status == "current",
                RiskAlert.expiry_kind != "legacy",
            )
        )
    )
    for alert in alerts:
        match = session.get(SupplierEventMatch, alert.match_id)
        alert.score_detail = {
            **(alert.score_detail or {}),
            "off_target_expiry": {
                "code": OFF_TARGET_EXPIRY_CODE,
                "event_id": event_id,
                "target_supplier_id": target_supplier_id,
                "off_target_supplier_id": match.supplier_id if match else None,
                "expired_at": now_utc.isoformat(),
            },
        }
        alert.status = "expired"
        alert.updated_at = now_utc
    return len(alerts)