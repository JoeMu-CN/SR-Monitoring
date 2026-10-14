import hashlib
import json
from typing import Protocol

from pydantic import ValidationError

from app.signals.identity import compute_dedup_key
from app.signals.schemas import ManualSignalDocument, ManualSignalInput

MAX_FILE_BYTES = 5 * 1024 * 1024


class SignalFileValidationError(ValueError):
    def __init__(self, errors: list[dict[str, object]]) -> None:
        super().__init__("风险信号文件校验失败")
        self.errors = errors


class SourceAdapter(Protocol):
    source_code: str

    def parse(self, data: bytes) -> list[ManualSignalInput]: ...

    def fingerprint(self, signal: ManualSignalInput) -> str: ...

    def dedup_key(self, signal: ManualSignalInput) -> str: ...


class ManualJsonAdapter:
    source_code = "manual-json"

    def parse(self, data: bytes) -> list[ManualSignalInput]:
        if not data:
            raise SignalFileValidationError([{"path": "文件", "message": "文件为空"}])
        if len(data) > MAX_FILE_BYTES:
            raise SignalFileValidationError(
                [{"path": "文件", "message": "文件不能超过 5MB"}]
            )
        try:
            payload = json.loads(data.decode("utf-8-sig"))
            document = ManualSignalDocument.model_validate(payload)
        except UnicodeDecodeError as exc:
            raise SignalFileValidationError(
                [{"path": "文件", "message": "文件必须使用 UTF-8 编码"}]
            ) from exc
        except json.JSONDecodeError as exc:
            raise SignalFileValidationError(
                [
                    {
                        "path": f"第 {exc.lineno} 行第 {exc.colno} 列",
                        "message": "不是有效的 JSON",
                    }
                ]
            ) from exc
        except ValidationError as exc:
            errors: list[dict[str, object]] = [
                {
                    "path": ".".join(str(part) for part in item["loc"]),
                    "message": item["msg"],
                }
                for item in exc.errors()
            ]
            raise SignalFileValidationError(errors) from exc
        return document.signals

    def fingerprint(self, signal: ManualSignalInput) -> str:
        canonical = json.dumps(
            signal.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def dedup_key(self, signal: ManualSignalInput) -> str:
        """手工 JSON 信源的去重身份。

        有 external_id 时只用它（同一外部标识即同一逻辑信号，标题/正文修订、
        URL 写法变化都不应重新落库）；缺 external_id 时回落到整模型哈希，
        与既有 :meth:`fingerprint` 口径一致，避免改变历史去重行为。
        """
        if signal.external_id:
            return compute_dedup_key(
                self.source_code,
                external_id=signal.external_id,
                url=str(signal.url) if signal.url else None,
                title=signal.title,
                fields=("external_id",),
            )
        return self.fingerprint(signal)
