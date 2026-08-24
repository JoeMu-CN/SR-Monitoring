"""风险提醒外部通知模块（Provider 适配层）。

设计约定（见《风险预警手机推送接入方案.md》）：
- 零侵入：推送独立于风险分析链，只读 risk_alerts，不改动 risks/engine 与既有 scheduler jobs。
- Provider 适配：渠道实现统一 send(title, content) 接口，新增渠道只需注册一个实现类。
- 防骚扰：同事件去重（alert 粒度）、P2 合并窗口、单渠道限频、免打扰时段。
- 可审计：每次投递写入 notification_deliveries，密钥不落库、不落日志。
"""

from app.notification.models import NotificationDelivery, NotificationSubscription
from app.notification.service import notify_job, scan_and_notify

__all__ = [
    "NotificationDelivery",
    "NotificationSubscription",
    "notify_job",
    "scan_and_notify",
]
