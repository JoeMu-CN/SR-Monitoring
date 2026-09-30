"""Agent 工具白名单。

所有工具只能读取业务库或调用外部核查网关，禁止任何写操作。
写操作（加入监控、启停供应商等）必须由用户在前端确认后走既有 API。
"""

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.budget import get_tyc_usage
from app.agent.tyc_gateway import TycGateway, build_tyc_gateway
from app.agent.tyc_quota import (
    NOT_CONFIGURED_MESSAGE,
    TycQuotaExecutionResult,
    TycQuotaOutcome,
    execute_tyc_tool_with_quota,
)
from app.risks.models import RiskAlert, RiskEvent, SupplierEventMatch
from app.risks.query_validity import current_alert_condition
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

    def __init__(self, *, now_utc: datetime | None = None) -> None:
        self.now_utc = now_utc

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        now_utc = self.now_utc or datetime.now(UTC)
        filters = [current_alert_condition(now_utc)]
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
                    "expires_at": alert.expires_at,
                    "expiry_kind": alert.expiry_kind,
                    "validity_state": event.validity_state,
                    "valid_until": event.valid_until,
                    "review_due_at": event.review_due_at,
                    "validity_policy_version": event.validity_policy_version,
                    "validity_reason": event.validity_reason,
                    "updated_at": alert.updated_at.isoformat(),
                }
                for alert, match, event, supplier in rows
            ],
        }


class VerifyCompanyTool:
    """企业风险核查（受预算控制器管控）。

    路由规则：
    - 清单内（enabled 供应商）：只读已入库的最新天眼查信号，不调 MCP、不消耗额度；
    - 清单外企业：实时调用天眼查 MCP，经共享单工具额度执行器
      （``execute_tyc_tool_with_quota``）在额度锁内重读余额、执行调用、记账并独立提交，
      与批量路径共享同一日/月额度，调用事实不随 Agent 会话事务回滚。
    """

    name = "verify_company"
    description = (
        "对任意企业做风险核查（工商、司法、经营异常）。清单内供应商返回已入库最新信息；"
        "清单外企业实时调用天眼查，受每日/每月调用额度限制。只读。"
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

        # 清单内供应商：读已入库最新信号，不触发实时天眼查
        supplier = session.scalar(
            select(Supplier).where(
                Supplier.enabled.is_(True),
                Supplier.legal_name == name,
            )
        )
        if supplier is not None:
            from app.agent.supplier_tyc import (
                format_tyc_signal_result,
                latest_tyc_signals_for_supplier,
            )

            signals = latest_tyc_signals_for_supplier(session, supplier)
            if signals:
                return format_tyc_signal_result(signals[0])
            return {
                "status": "empty",
                "source": "database",
                "message": "该供应商暂无已入库的天眼查核查记录（定时核查尚未执行或未命中）",
                "supplier_id": supplier.id,
                "supplier_code": supplier.supplier_code,
            }

        # 清单外企业：实时调用（受预算控制器管控）
        usage = get_tyc_usage(session)
        if not usage.enabled:
            return {
                "status": "not_configured",
                "message": NOT_CONFIGURED_MESSAGE,
                "usage": usage.to_dict(),
            }

        gateway = self.gateway or build_tyc_gateway(session=session)

        async def remote_call() -> dict[str, object]:
            return await gateway.verify(name)

        execution = await execute_tyc_tool_with_quota(self.name, name, remote_call)
        return _quota_execution_response(execution)


_RESPONSE_STATUS_BY_OUTCOME: dict[TycQuotaOutcome, str] = {
    TycQuotaOutcome.SUCCESS_WITH_RECORDS: "success",
    TycQuotaOutcome.EMPTY: "empty",
    TycQuotaOutcome.ERROR: "error",
    TycQuotaOutcome.QUOTA_EXHAUSTED: "quota_exhausted",
    TycQuotaOutcome.BUSY: "busy",
    TycQuotaOutcome.NOT_CONFIGURED: "not_configured",
}


def _quota_execution_response(execution: TycQuotaExecutionResult) -> dict[str, object]:
    """执行器结果 → VerifyCompanyTool 对外响应（保留 status/message/usage 合同）。

    远程正常返回时原样透出网关 payload（``status``/``company_name`` 等键不变）
    并补充执行器提交后的 ``usage`` 快照；远程异常、未启用（锁内撤销）、额度耗尽
    与额度繁忙返回结构化 ``status + message``。
    """
    usage = execution.usage.to_dict() if execution.usage is not None else None
    if execution.payload is not None:
        response = dict(execution.payload)
        response["usage"] = usage
        return response
    return {
        "status": _RESPONSE_STATUS_BY_OUTCOME[execution.outcome],
        "message": execution.message,
        "usage": usage,
    }


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


def build_tools(*, now_utc: datetime | None = None) -> list[Tool]:
    """风险查询 Agent 的永久只读工具白名单。"""
    return [
        QuerySuppliersTool(),
        QueryCurrentAlertsTool(now_utc=now_utc),
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
