"""Agent 工具白名单。

所有工具只能读取业务库或调用外部核查网关，禁止任何写操作。
写操作（加入监控、启停供应商等）必须由用户在前端确认后走既有 API。
"""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budget import get_tyc_usage, record_tyc_usage
from app.agent.tyc_gateway import TycGateway, build_tyc_gateway
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.suppliers.models import Supplier, SupplierProduct, SupplierSite

MAX_ALERT_RESULTS = 50
MAX_SUPPLIER_RESULTS = 50
RESULT_TEXT_LIMIT = 4000


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, object]

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]: ...


class QuerySuppliersTool:
    name = "query_suppliers"
    description = "按关键词查询监控清单内的供应商及其生产地点、供应产品。只读。"
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "供应商名称、地点或产品关键词，可省略"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SUPPLIER_RESULTS},
        },
    }

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        keyword = str(arguments.get("keyword") or "").strip()
        limit = _bounded_int(arguments.get("limit"), 10, MAX_SUPPLIER_RESULTS)
        query = select(Supplier).where(Supplier.enabled.is_(True))
        if keyword:
            query = query.where(
                Supplier.legal_name.ilike(f"%{keyword}%")
                | Supplier.supplier_code.ilike(f"%{keyword}%")
            )
        suppliers = list(session.scalars(query.order_by(Supplier.supplier_code).limit(limit)))
        return {
            "total": len(suppliers),
            "items": [
                {
                    "id": s.id,
                    "supplier_code": s.supplier_code,
                    "legal_name": s.legal_name,
                    "country_code": s.country_code,
                    "registry_no": s.registry_no,
                    "sites": [
                        {"site_name": site.site_name, "city": site.city, "address": site.address}
                        for site in _sites(session, s.id)
                    ],
                    "products": [
                        {"name": p.name, "keywords": p.keywords}
                        for p in _products(session, s.id)
                    ],
                }
                for s in suppliers
            ],
        }


class QueryCurrentAlertsTool:
    name = "query_current_alerts"
    description = "查询当前有效的 P1-P4 风险提醒，可按等级、供应商、城市和产品筛选。只读。"
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["P1", "P2", "P3", "P4"]},
            "supplier_name": {"type": "string"},
            "city": {"type": "string"},
            "product": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ALERT_RESULTS},
        },
    }

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        filters = [RiskAlert.status == "current"]
        if level := str(arguments.get("level") or "").strip():
            filters.append(RiskAlert.level == level)
        supplier_name = str(arguments.get("supplier_name") or "").strip()
        if supplier_name:
            filters.append(Supplier.legal_name.ilike(f"%{supplier_name}%"))
        city = str(arguments.get("city") or "").strip()
        if city:
            filters.append(SupplierSite.city.ilike(f"%{city}%"))
        product = str(arguments.get("product") or "").strip()
        if product:
            filters.append(SupplierProduct.name.ilike(f"%{product}%"))
        limit = _bounded_int(arguments.get("limit"), 20, MAX_ALERT_RESULTS)

        query = (
            select(RiskAlert, SupplierEventMatch, RiskEvent, Supplier)
            .join(SupplierEventMatch, RiskAlert.match_id == SupplierEventMatch.id)
            .join(RiskEvent, SupplierEventMatch.event_id == RiskEvent.id)
            .join(Supplier, SupplierEventMatch.supplier_id == Supplier.id)
        )
        if city:
            query = query.join(SupplierSite, SupplierSite.supplier_id == Supplier.id)
        if product:
            query = query.join(SupplierProduct, SupplierProduct.supplier_id == Supplier.id)
        rows = session.execute(
            query.where(*filters)
            .order_by(RiskAlert.level, RiskAlert.updated_at.desc(), RiskAlert.id.desc())
            .limit(limit)
        ).all()

        return {
            "total": len(rows),
            "items": [
                {
                    "alert_id": alert.id,
                    "level": alert.level,
                    "score": alert.score,
                    "status": alert.status,
                    "supplier_id": supplier.id,
                    "supplier_name": supplier.legal_name,
                    "event_id": event.id,
                    "event_type": event.event_type,
                    "event_summary": event.summary,
                    "match_reasons": match.reasons,
                    "match_evidence": match.evidence,
                    "updated_at": alert.updated_at.isoformat(),
                }
                for alert, match, event, supplier in rows
            ],
        }


class VerifyCompanyTool:
    """清单外企业一次性核查（受预算控制器管控）。"""

    name = "verify_company"
    description = (
        "对任意企业做一次性风险核查（工商、司法、经营异常）。"
        "只读，受每日/每月调用额度限制。"
    )
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {"company_name": {"type": "string", "minLength": 1}},
        "required": ["company_name"],
    }

    def __init__(self, gateway: TycGateway | None = None) -> None:
        self.gateway = gateway

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        name = str(arguments.get("company_name") or "").strip()
        if not name:
            return {"status": "error", "message": "company_name 不能为空"}

        usage = get_tyc_usage(session)
        if not usage.enabled:
            return {
                "status": "not_configured",
                "message": "天眼查未启用：请在数据源控制台配置运行密钥并启用",
                "usage": usage.to_dict(),
            }
        if not usage.allowed:
            return {
                "status": "quota_exhausted",
                "message": (
                    f"天眼查额度已达上限：今日 {usage.daily_used}/{usage.daily_limit}，"
                    f"本月 {usage.monthly_used}/{usage.monthly_limit}"
                ),
                "usage": usage.to_dict(),
            }

        gateway = self.gateway or build_tyc_gateway(session=session)
        try:
            result = await gateway.verify(name)
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "message": f"天眼查调用失败：{exc}"[:500]}
        call_status = _classify_call(result)
        record_tyc_usage(
            session,
            tool_name=self.name,
            company_name=name,
            status=call_status,
        )
        result["usage"] = get_tyc_usage(session).to_dict()
        return result


def _classify_call(result: dict[str, object]) -> str:
    """按天眼查计费口径归类调用结果：只有 success 计 1 次。"""
    status = str(result.get("status") or "error")
    if status == "success":
        return "success"
    if status in {"empty", "error", "not_configured"}:
        return status
    return "error"


class GetBudgetTool:
    name = "get_budget"
    description = "查询 Agent 与天眼查调用的额度余量（真实计数）。只读。"
    parameters: dict[str, object] = {"type": "object", "properties": {}}

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        return get_tyc_usage(session).to_dict()


def _bounded_int(value: object, default: int, maximum: int) -> int:
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
        parsed = int(value)
    else:
        return default
    if parsed < 1:
        return default
    return min(parsed, maximum)


def _sites(session: Session, supplier_id: int) -> list[SupplierSite]:
    return list(
        session.scalars(
            select(SupplierSite)
            .where(SupplierSite.supplier_id == supplier_id)
            .order_by(SupplierSite.site_name)
        )
    )


def _products(session: Session, supplier_id: int) -> list[SupplierProduct]:
    return list(
        session.scalars(
            select(SupplierProduct)
            .where(SupplierProduct.supplier_id == supplier_id)
            .order_by(SupplierProduct.name)
        )
    )


def build_tools() -> list[Tool]:
    """风险查询 Agent 的永久只读工具白名单。"""
    return [
        QuerySuppliersTool(),
        QueryCurrentAlertsTool(),
        VerifyCompanyTool(),
        GetBudgetTool(),
    ]


def build_tool_specs(tools: list[Tool]) -> list[dict[str, object]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in tools
    ]
