"""一次性脚本：把 mem-incident-bulletin 信源的 adapter_config 写入数据库。

使用方法（容器内执行，绕过 HTTP API）：
    docker compose exec -T app python /workspace/scripts/seed_zhsgxx_adapter.py

回退方法：
    docker compose exec -T postgres psql -U supplier_risk -d supplier_risk \\
        -c "UPDATE data_sources SET adapter_config='{}'::jsonb, adapter_status='unconfigured', enabled=false WHERE code='mem-incident-bulletin';"

为什么写脚本而非走 API：
- 一次性任务不需要入产品代码；
- 走 API 需要登录态 + admin 权限 + 多步（draft→preview→publish），脚本路径清晰；
- 失败易回退（一条 UPDATE 即可重置）。
"""
from __future__ import annotations

import json
import sys

from sqlalchemy import text

from app.database import SessionLocal

SPEC_JSON = {
    "format": "html",
    "request": {
        "method": "GET",
        "url": "https://www.mem.gov.cn/xw/zhsgxx/",
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.mem.gov.cn/",
        },
        "timeout_seconds": 15,
    },
    # 列表项 selector：直接选 a[href*=yjglbgzdt][href$=.shtml]（不再用 li），
    # 避免顶部导航/搜索框等 li 元素混入。
    "items_selector": 'a[href*="yjglbgzdt"][href$=".shtml"]',
    "mapping": {
        "title": "self",
        "content": "self",  # 列表页无正文，self 取 a 文本（含日期）作内容
        "url": "self@href",
        # 注意：真实页面日期格式为 "2026-08-11 20:12"（非 ISO 8601），
        # _parse_datetime 会拒收；因此不映射 published_at，日期保留在 content 文本中。
    },
    "fingerprint_fields": ["external_id", "title", "content", "url"],
    "max_items": 20,  # 首页固定 20 条
    "use_crawl4ai_fallback": True,
}


def main() -> int:
    payload = json.dumps(SPEC_JSON, ensure_ascii=False, sort_keys=True)
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id, code, adapter_status FROM data_sources "
                "WHERE code = 'mem-incident-bulletin'"
            )
        ).first()
        if row is None:
            print("ERROR: data_sources row for mem-incident-bulletin not found", file=sys.stderr)
            return 1
        data_source_id, code, prev_status = row
        session.execute(
            text(
                "UPDATE data_sources SET adapter_config = CAST(:cfg AS jsonb), "
                "adapter_status = 'draft', endpoint_url = 'https://www.mem.gov.cn/xw/zhsgxx/' "
                "WHERE id = :id"
            ),
            {"cfg": payload, "id": data_source_id},
        )
        session.commit()
        print(
            f"updateded data_source id={data_source_id} code={code} "
            f"adapter_status: {prev_status} -> draft; "
            f"endpoint_url -> https://www.mem.gov.cn/xw/zhsgxx/"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())