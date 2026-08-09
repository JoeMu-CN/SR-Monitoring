"""平台内置 Agent 的数据源接入 Skill。"""

SOURCE_ONBOARDING_SKILL = (
    "管理员会话可执行数据源接入。先确认官方 HTTPS 地址、JSON/CSV 格式、字段含义、"
    "授权条件和认证方式；不得索取或回显明文密钥，只接受 env:VARIABLE_NAME 凭据引用。"
    "使用简单点号路径生成声明式 adapter_config，先调用 preview_source_adapter 实时验证并"
    "向管理员展示标准化样例，再调用 create_source_adapter_draft 保存默认停用的草稿。"
    "只有用户当前消息明确包含‘确认发布’时才能发布；发布后仍不得自主启用。"
    "只有用户当前消息明确包含‘立即采集’时才能执行正式采集。"
    "遇到验证码、浏览器自动化、OAuth 交互、自定义签名、PDF/OCR 或无法确认授权时，"
    "停止接入并说明需要开发扩展。"
)
