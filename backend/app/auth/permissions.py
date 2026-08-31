"""认证角色的稳定权限矩阵。"""

from app.config import RESEARCH_TRACK_ENABLED

PERM_RISK_VIEW = "risk_view"
PERM_SUPPLIER_VIEW = "supplier_view"
PERM_SOURCE_STATUS_VIEW = "source_status_view"
PERM_RULE_SUMMARY_VIEW = "rule_summary_view"
PERM_RISK_QUERY_USE = "risk_query_use"
PERM_EXTERNAL_VERIFICATION = "external_verification"
PERM_REPORT_EXPORT = "report_export"
PERM_SUPPLIER_MANAGE = "supplier_manage"
PERM_SIGNAL_IMPORT = "signal_import"
PERM_ANALYSIS_RUN = "analysis_run"
PERM_SOURCE_MANAGE = "source_manage"
PERM_COLLECTION_TRIGGER = "collection_trigger"
PERM_SOURCE_AGENT_USE = "source_agent_use"
PERM_RULE_MANAGE = "rule_manage"
PERM_BUSINESS_AUDIT_VIEW = "business_audit_view"
PERM_USER_MANAGE = "user_manage"
PERM_SESSION_REVOKE = "session_revoke"
PERM_SECURITY_AUDIT_VIEW = "security_audit_view"
PERM_AUTH_CONFIG_MANAGE = "auth_config_manage"
PERM_RESEARCH_TASK_CREATE = "research_task_create"
PERM_RESEARCH_SCHEDULE_MANAGE = "research_schedule_manage"
PERM_RESEARCH_CLAIM_PROMOTE = "research_claim_promote"
PERM_RESEARCH_PROVIDER_MANAGE = "research_provider_manage"

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
    },
    "risk_analyst": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
        PERM_RISK_QUERY_USE,
        PERM_EXTERNAL_VERIFICATION,
        PERM_REPORT_EXPORT,
        PERM_RESEARCH_TASK_CREATE,
    },
    "risk_admin": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
        PERM_RISK_QUERY_USE,
        PERM_EXTERNAL_VERIFICATION,
        PERM_REPORT_EXPORT,
        PERM_RESEARCH_TASK_CREATE,
        PERM_RESEARCH_SCHEDULE_MANAGE,
        PERM_RESEARCH_CLAIM_PROMOTE,
        PERM_SUPPLIER_MANAGE,
        PERM_SIGNAL_IMPORT,
        PERM_ANALYSIS_RUN,
        PERM_SOURCE_MANAGE,
        PERM_COLLECTION_TRIGGER,
        PERM_SOURCE_AGENT_USE,
        PERM_RULE_MANAGE,
        PERM_BUSINESS_AUDIT_VIEW,
    },
    "platform_admin": {
        PERM_RISK_VIEW,
        PERM_SUPPLIER_VIEW,
        PERM_SOURCE_STATUS_VIEW,
        PERM_RULE_SUMMARY_VIEW,
        PERM_RISK_QUERY_USE,
        PERM_EXTERNAL_VERIFICATION,
        PERM_REPORT_EXPORT,
        PERM_SUPPLIER_MANAGE,
        PERM_SIGNAL_IMPORT,
        PERM_ANALYSIS_RUN,
        PERM_SOURCE_MANAGE,
        PERM_COLLECTION_TRIGGER,
        PERM_SOURCE_AGENT_USE,
        PERM_RULE_MANAGE,
        PERM_BUSINESS_AUDIT_VIEW,
        PERM_USER_MANAGE,
        PERM_SESSION_REVOKE,
        PERM_SECURITY_AUDIT_VIEW,
        PERM_AUTH_CONFIG_MANAGE,
        PERM_RESEARCH_TASK_CREATE,
        PERM_RESEARCH_SCHEDULE_MANAGE,
        PERM_RESEARCH_CLAIM_PROMOTE,
        PERM_RESEARCH_PROVIDER_MANAGE,
    },
}


def role_permissions(role: str) -> list[str]:
    permissions = set(ROLE_PERMISSIONS.get(role, set()))
    if not RESEARCH_TRACK_ENABLED:
        permissions.difference_update(
            {
                PERM_RESEARCH_TASK_CREATE,
                PERM_RESEARCH_SCHEDULE_MANAGE,
                PERM_RESEARCH_CLAIM_PROMOTE,
                PERM_RESEARCH_PROVIDER_MANAGE,
            }
        )
    return sorted(permissions)
