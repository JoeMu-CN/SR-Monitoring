"""一次性脚本：把 mofcom-entity-control 信源的 adapter_config 写入数据库。

信源：商务部产业安全与进出口管制局（aqygzj.mofcom.gov.cn）
- 该子站仅支持 HTTP 明文（HTTPS TLS 反爬：SSLV3_ALERT_HANDSHAKE_FAILURE），
  故走 allow_http_hosts 白名单放行（只读抓取公开政府公告）。
- 首页静态 HTML 含约 21 条公告/新闻（出口管制管控名单、不可靠实体清单、
  反制措施等），列表页 /flzc/gzjgfxwj/ 是 JS 渲染，故抓首页。

使用方法（容器内执行）：
    docker compose exec -T app python /tmp/seed_mofcom_adapter.py

回退方法：
    docker compose exec -T postgres psql -U supplier_risk -d supplier_risk \\
        -c "UPDATE data_sources SET adapter_config='{}'::jsonb, adapter_status='unconfigured', enabled=false WHERE code='mofcom-entity-control';"
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
        "url": "http://aqygzj.mofcom.gov.cn/",
        # 源站仅 HTTP 明文；白名单放行（默认校验拒绝 HTTP）
        "allow_http_hosts": ["aqygzj.mofcom.gov.cn"],
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        "timeout_seconds": 20,
    },
    # 首页静态列表项：直接选文章链接 a[href*=art][href$=".html"] 的相对路径形式
    "items_selector": 'a[href*="art/"][href$=".html"]',
    "mapping": {
        "title": "self",
        "content": "self",  # 列表页无正文，self 取链接文本作内容
        "url": "self@href",
    },
    "fingerprint_fields": ["external_id", "title", "content", "url"],
    "max_items": 25,
    "use_crawl4ai_fallback": True,
}


def main() -> int:
    payload = json.dumps(SPEC_JSON, ensure_ascii=False, sort_keys=True)
    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT id, code, adapter_status FROM data_sources "
                "WHERE code = 'mofcom-entity-control'"
            )
        ).first()
        if row is None:
            print("ERROR: data_sources row for mofcom-entity-control not found", file=sys.stderr)
            return 1
        data_source_id, code, prev_status = row
        session.execute(
            text(
                "UPDATE data_sources SET adapter_config = CAST(:cfg AS jsonb), "
                "adapter_status = 'draft', "
                "endpoint_url = 'http://aqygzj.mofcom.gov.cn/' "
                "WHERE id = :id"
            ),
            {"cfg": payload, "id": data_source_id},
        )
        session.commit()
        print(
            f"updated data_source id={data_source_id} code={code} "
            f"adapter_status: {prev_status} -> draft; "
            f"endpoint_url -> http://aqygzj.mofcom.gov.cn/"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
