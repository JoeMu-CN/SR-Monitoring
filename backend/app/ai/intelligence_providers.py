"""情报的模型 Provider（Fake + OpenAI 兼容），覆盖门控与报告两种调用。

为什么不复用 ``app.ai.providers`` 的 ``AIProvider`` 协议：该协议的
``analyze_signal`` / ``generate_research_report`` 两条通道的输入输出 Schema
分别属于信号解析与研究报告，都不是「门控 + 简要报告」，而该模块由
其它任务维护、不在本任务可改范围。因此这里按同一套 Provider 模式另建一条
通道：完全一致的错误类型（复用 ``AIProviderError`` / ``AIConfigurationError``）、
一致的 ``temperature=0`` + ``json_object`` 结构化输出、一致的指数退避重试
与「确定性校验失败不重试」策略、一致的 ``strip_code_fence`` 解析。

两个方法共用同一个 Provider 实例与同一套重试/校验逻辑，只差系统提示词与输出
Schema：``gate_intelligence`` 判是否收录，``generate_intelligence`` 写报告。

``provider_name`` / ``model`` 与 ``app.ai.providers`` 同名同义，配置仍来自
``get_ai_settings()``：``AI_PROVIDER=fake`` 时不发起任何网络请求。
"""

import asyncio
import json
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.intelligence_prompt import (
    intelligence_gate_system_prompt,
    intelligence_system_prompt,
)
from app.ai.intelligence_schemas import (
    IntelligenceGateInput,
    IntelligenceGateOutput,
    IntelligenceInput,
    IntelligenceOutput,
)
from app.ai.providers import (
    AIConfigurationError,
    AIProviderError,
    strip_code_fence,
)
from app.config import AISettings

_OutputT = TypeVar("_OutputT", bound=BaseModel)


class _RetryableIntelligenceError(AIProviderError):
    pass


class IntelligenceProvider(Protocol):
    provider_name: str
    model: str

    async def generate_intelligence(
        self, value: IntelligenceInput
    ) -> IntelligenceOutput: ...

    async def gate_intelligence(
        self, value: IntelligenceGateInput
    ) -> IntelligenceGateOutput: ...


class FakeIntelligenceProvider:
    """离线确定性 Provider：不访问网络，返回结构合法的占位报告。

    门控一律放行：离线模式没有判定语义的能力，静默丢弃数据比保留噪声更糟，
    也让本地演示与既有端到端断言保持稳定。需要演练过滤路径时在测试里覆盖
    ``gate_intelligence``（见 ``tests/test_intelligence_gate.py``）。
    """

    provider_name = "fake"
    model = "fake-deterministic-v1"

    async def generate_intelligence(
        self, value: IntelligenceInput
    ) -> IntelligenceOutput:
        return IntelligenceOutput(
            report_zh=f"本地模拟情报解读：{value.title}。{value.summary}",
            suggestions=[f"本地模拟提醒：持续跟踪 {value.source_name} 的后续公告。"],
        )

    async def gate_intelligence(
        self, value: IntelligenceGateInput
    ) -> IntelligenceGateOutput:
        del value
        return IntelligenceGateOutput(
            relevant=True, reason_zh="离线 Provider 不做相关性判定，一律放行"
        )


class OpenAICompatibleIntelligenceProvider:
    """OpenAI Chat Completions 兼容 Provider；重试与错误分级见模块 docstring。"""

    provider_name = "openai-compatible"

    def __init__(
        self,
        settings: AISettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        if not settings.base_url or not settings.model or not settings.api_key:
            raise AIConfigurationError("OpenAI 兼容模型配置不完整")
        self.settings = settings
        self.model = settings.model
        self.transport = transport
        self.retry_delay_seconds = retry_delay_seconds

    async def generate_intelligence(
        self, value: IntelligenceInput
    ) -> IntelligenceOutput:
        return await self._call(
            kind="情报报告",
            system_prompt=intelligence_system_prompt(value.category),
            value=value,
            output_model=IntelligenceOutput,
        )

    async def gate_intelligence(
        self, value: IntelligenceGateInput
    ) -> IntelligenceGateOutput:
        return await self._call(
            kind="情报门控",
            system_prompt=intelligence_gate_system_prompt(value.category),
            value=value,
            output_model=IntelligenceGateOutput,
        )

    async def _call(
        self,
        *,
        kind: str,
        system_prompt: str,
        value: BaseModel,
        output_model: type[_OutputT],
    ) -> _OutputT:
        for attempt in range(self.settings.max_retries + 1):
            try:
                return await self._request(
                    kind=kind,
                    system_prompt=system_prompt,
                    value=value,
                    output_model=output_model,
                )
            except _RetryableIntelligenceError as exc:
                if attempt >= self.settings.max_retries:
                    raise AIProviderError(f"{kind}模型请求在重试后仍失败") from exc
                await asyncio.sleep(self.retry_delay_seconds * (2**attempt))
        raise AIProviderError(f"{kind}模型请求失败")

    async def _request(
        self,
        *,
        kind: str,
        system_prompt: str,
        value: BaseModel,
        output_model: type[_OutputT],
    ) -> _OutputT:
        endpoint = f"{self.settings.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        value.model_dump(mode="json"), ensure_ascii=False
                    ),
                },
            ],
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds, transport=self.transport
            ) as client:
                response = await client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {self.settings.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _RetryableIntelligenceError(f"{kind}模型网络请求失败") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise _RetryableIntelligenceError(f"{kind}模型服务暂时不可用")
        if response.status_code >= 400:
            raise AIProviderError(
                f"{kind}模型请求失败（HTTP {response.status_code}）"
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            payload_json = json.loads(strip_code_fence(content))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise _RetryableIntelligenceError(
                f"{kind}模型返回的结构化结果无效"
            ) from exc
        try:
            validated: _OutputT = output_model.model_validate(payload_json)
        except ValidationError as exc:
            # 字段缺失/类型错误在 temperature=0 下重试只会重复消耗额度，不修复同一
            # 提示词的语义约束问题，因此与 providers.py 同策略：确定性失败不重试。
            raise AIProviderError(f"{kind}模型返回的结构化结果无效") from exc
        return validated


def build_intelligence_provider(settings: AISettings) -> IntelligenceProvider:
    """按配置构建情报 Provider（门控与报告共用同一实例）。"""
    if settings.provider == "fake":
        return FakeIntelligenceProvider()
    if settings.provider == "openai-compatible":
        return OpenAICompatibleIntelligenceProvider(settings)
    raise AIConfigurationError(f"不支持的 AI_PROVIDER：{settings.provider}")
