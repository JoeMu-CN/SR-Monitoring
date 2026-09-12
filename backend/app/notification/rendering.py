"""通知消息渲染：单条提醒与合并摘要的标题、正文与详情链接。

从 service.py 拆出；详情链接固定 ``frontend_url.rstrip('/') + '/risks/{id}'``。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier


def alert_context(session: Session, alert: RiskAlert) -> tuple[str, str, str, list[str]]:
    """渲染输入装配：查询提醒关联的供应商与事件信息。"""
    supplier_name = "未知供应商"
    event_type = ""
    event_summary = ""
    reasons: list[str] = []
    match = session.get(SupplierEventMatch, alert.match_id)
    if match is not None:
        supplier = session.get(Supplier, match.supplier_id)
        if supplier is not None:
            supplier_name = supplier.legal_name
        reasons = list(match.reasons or [])
        event = session.get(RiskEvent, match.event_id)
        if event is not None:
            event_type = event.event_type
            event_summary = event.summary
    return supplier_name, event_type, event_summary, reasons


def detail_link(frontend_url: str, alert_id: int) -> str:
    """风险详情页链接（详情路径 /risks/{id}）。"""
    return f"{frontend_url.rstrip('/')}/risks/{alert_id}"


def render_alert_payload(
    alert: RiskAlert,
    supplier_name: str,
    event_type: str,
    summary: str,
    reasons: list[str],
    frontend_url: str,
) -> tuple[str, str]:
    """渲染单条提醒标题与正文（敏感信息不出现）。"""
    title = f"【风险预警 {alert.level}】供应商「{supplier_name}」"
    lines = [
        f"维度：{event_type}",
        f"事件：{(summary or '')[:80]}",
    ]
    if reasons:
        lines.append(f"匹配：{'; '.join(str(r) for r in reasons[:2])}")
    if frontend_url:
        lines.append(f"平台：{detail_link(frontend_url, alert.id)}")
    return title, "\n".join(lines)


@dataclass(frozen=True)
class DigestMember:
    """摘要成员：保留 alert 归属，逐成员渲染标识与各自链接。"""

    level: str
    supplier_name: str
    event_type: str
    alert_id: int


def render_digest(
    members: list[DigestMember], frontend_url: str
) -> tuple[str, str]:
    """把多条成员渲染为一条摘要；每成员含供应商标识与各自详情路径。"""
    title = f"【风险预警汇总】共 {len(members)} 条提醒"
    lines: list[str] = []
    for idx, member in enumerate(members[:10], start=1):
        dimension = f" {member.event_type}" if member.event_type else ""
        line = f"{idx}. 【{member.level}】供应商「{member.supplier_name}」{dimension}"
        if frontend_url:
            line += f" 平台：{detail_link(frontend_url, member.alert_id)}"
        lines.append(line)
    if len(members) > 10:
        lines.append(f"…另有 {len(members) - 10} 条，请登录平台查看")
    return title, "\n".join(lines)
