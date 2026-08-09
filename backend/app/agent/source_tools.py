"""仅管理员会话可用的数据源接入工具。"""

from datetime import UTC, datetime
import re

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.signals.declarative import AdapterSpec, preview_adapter
from app.signals.models import DataSource, DataSourceAuditLog
from app.signals.router import build_pull_adapter
from app.signals.service import collect_source_async
from app.signals.sources import SourceFetchError

_ENV_REF = re.compile(r"env:[A-Z][A-Z0-9_]{0,127}\Z")


class PreviewSourceAdapterTool:
    name = "preview_source_adapter"
    description = "按声明式配置实时访问官方 HTTPS 数据源并预览最多 10 条标准化信号，不落库。"
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {
            "source_code": {"type": "string"},
            "adapter_config": {"type": "object"},
            "auth_type": {"type": "string", "enum": ["none", "api_key", "bearer"]},
            "credential_ref": {"type": ["string", "null"]},
            "login_config": {"type": "object"},
        },
        "required": ["source_code", "adapter_config"],
    }

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        del session
        try:
            spec = AdapterSpec.model_validate(arguments.get("adapter_config"))
            result = await preview_adapter(
                str(arguments.get("source_code") or "preview-source"),
                spec,
                auth_type=str(arguments.get("auth_type") or "none"),
                credential_ref=_optional_text(arguments.get("credential_ref")),
                login_config=_login_config(arguments.get("login_config")),
            )
        except (ValueError, SourceFetchError) as exc:
            return {"status": "error", "message": str(exc)[:500]}
        return {
            "status": "success",
            "fetched_count": result.fetched_count,
            "items": [item.model_dump(mode="json") for item in result.items],
        }


class CreateSourceAdapterDraftTool:
    name = "create_source_adapter_draft"
    description = "校验并保存一个默认停用的声明式数据源草稿。不会发布或启用。"
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "name": {"type": "string"},
            "source_type": {"type": "string"},
            "credibility": {"type": "integer", "minimum": 0, "maximum": 100},
            "schedule": {"type": ["string", "null"]},
            "auth_type": {"type": "string", "enum": ["none", "api_key", "bearer"]},
            "credential_ref": {"type": ["string", "null"]},
            "login_config": {"type": "object"},
            "description": {"type": ["string", "null"]},
            "adapter_config": {"type": "object"},
        },
        "required": ["code", "name", "source_type", "credibility", "adapter_config"],
    }

    def __init__(self, actor_id: str | None) -> None:
        self.actor_id = actor_id

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        code = str(arguments.get("code") or "").strip()
        name = str(arguments.get("name") or "").strip()
        source_type = str(arguments.get("source_type") or "").strip()
        if not code or not name or not source_type:
            return {"status": "error", "message": "code、name、source_type 不能为空"}
        if session.scalar(select(DataSource).where(DataSource.code == code)) is not None:
            return {"status": "error", "message": "数据源编码已存在"}
        try:
            credibility = _int_value(arguments.get("credibility"))
            if not 0 <= credibility <= 100:
                raise ValueError
            spec = AdapterSpec.model_validate(arguments.get("adapter_config"))
        except (TypeError, ValueError, ValidationError):
            return {"status": "error", "message": "可信度或适配器配置无效"}
        schedule = _optional_text(arguments.get("schedule"))
        if schedule and len(schedule.split()) != 5:
            return {"status": "error", "message": "调度周期必须是 5 段 cron"}
        auth_type = str(arguments.get("auth_type") or "none")
        if auth_type not in {"none", "api_key", "bearer"}:
            return {"status": "error", "message": "声明式适配器认证方式无效"}
        credential_ref = _optional_text(arguments.get("credential_ref"))
        if auth_type != "none" and not _valid_credential_ref(credential_ref):
            return {"status": "error", "message": "认证来源必须使用 env:VARIABLE_NAME 凭据引用"}
        try:
            login_config = _login_config(arguments.get("login_config"))
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        source = DataSource(
            code=code,
            name=name,
            source_type=source_type,
            credibility=credibility,
            schedule=schedule,
            endpoint_url=spec.request.url,
            auth_type=auth_type,
            login_config=login_config,
            credential_ref=credential_ref,
            description=_optional_text(arguments.get("description")),
            adapter_config=spec.model_dump(mode="json"),
            adapter_status="draft",
            adapter_version=0,
            enabled=False,
        )
        session.add(source)
        session.flush()
        _audit(session, source, "adapter_draft_created", self.actor_id, {"code": code})
        session.commit()
        session.refresh(source)
        return {"status": "success", "source_id": source.id, "adapter_status": "draft"}


class PublishSourceAdapterTool:
    name = "publish_source_adapter"
    description = (
        "实时验证并发布数据源适配器；发布后仍保持停用。"
        "仅在用户当前消息明确说‘确认发布’时可用。"
    )
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {
            "source_id": {"type": "integer"},
            "confirmation": {"type": "string", "enum": ["确认发布"]},
        },
        "required": ["source_id", "confirmation"],
    }

    def __init__(self, actor_id: str | None) -> None:
        self.actor_id = actor_id

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        if arguments.get("confirmation") != "确认发布":
            return {"status": "confirmation_required", "message": "请管理员明确输入“确认发布”"}
        source = session.get(DataSource, _int_value(arguments.get("source_id")))
        if source is None or not source.adapter_config:
            return {"status": "error", "message": "数据源或适配器草稿不存在"}
        try:
            spec = AdapterSpec.model_validate(source.adapter_config)
            preview = await preview_adapter(
                source.code,
                spec,
                auth_type=source.auth_type,
                credential_ref=source.credential_ref,
                login_config=source.login_config,
            )
        except (ValidationError, SourceFetchError) as exc:
            source.adapter_status = "invalid"
            source.enabled = False
            _audit(
                session,
                source,
                "adapter_validation_failed",
                self.actor_id,
                {"message": str(exc)[:500]},
            )
            session.commit()
            return {"status": "error", "message": str(exc)[:500]}
        source.adapter_status = "published"
        source.adapter_version += 1
        source.adapter_published_at = datetime.now(UTC)
        source.enabled = False
        _audit(
            session,
            source,
            "adapter_published",
            self.actor_id,
            {"adapter_version": source.adapter_version},
        )
        session.commit()
        return {
            "status": "success",
            "source_id": source.id,
            "adapter_version": source.adapter_version,
            "preview_count": preview.fetched_count,
            "enabled": False,
        }


class RunSourceNowTool:
    name = "run_source_now"
    description = "立即正式采集一个已发布且已启用的数据源。仅在用户当前消息明确说“立即采集”时可用。"
    parameters: dict[str, object] = {
        "type": "object",
        "properties": {
            "source_id": {"type": "integer"},
            "confirmation": {"type": "string", "enum": ["立即采集"]},
        },
        "required": ["source_id", "confirmation"],
    }

    async def execute(
        self, arguments: dict[str, object], session: Session
    ) -> dict[str, object]:
        if arguments.get("confirmation") != "立即采集":
            return {"status": "confirmation_required", "message": "请管理员明确输入“立即采集”"}
        source = session.get(DataSource, _int_value(arguments.get("source_id")))
        if source is None:
            return {"status": "error", "message": "数据源不存在"}
        if not source.enabled:
            return {"status": "error", "message": "数据源尚未启用"}
        try:
            adapter = build_pull_adapter(source)
            run = await collect_source_async(session, source, adapter)
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": str(exc)[:500]}
        return {
            "status": "success",
            "run_id": run.id,
            "fetched_count": run.fetched_count,
            "created_count": run.created_count,
            "duplicate_count": run.duplicate_count,
        }


def build_source_onboarding_tools(
    *, actor_id: str | None, allow_publish: bool, allow_run: bool
) -> list[object]:
    tools: list[object] = [PreviewSourceAdapterTool(), CreateSourceAdapterDraftTool(actor_id)]
    if allow_publish:
        tools.append(PublishSourceAdapterTool(actor_id))
    if allow_run:
        tools.append(RunSourceNowTool())
    return tools


def _audit(
    session: Session,
    source: DataSource,
    action: str,
    actor_id: str | None,
    changes: dict[str, object],
) -> None:
    session.add(
        DataSourceAuditLog(
            source_id=source.id,
            action=action,
            actor_role="admin",
            actor_id=actor_id,
            changes=changes,
        )
    )


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _login_config(value: object) -> dict[str, object]:
    config = _dict(value)
    unknown = set(config) - {"header_name"}
    if unknown:
        raise ValueError("login_config 只允许配置 header_name，不得包含密钥")
    return config


def _valid_credential_ref(value: str | None) -> bool:
    return bool(value and _ENV_REF.fullmatch(value))


def _int_value(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return 0
