export type RiskLevel = 'P1' | 'P2' | 'P3' | 'P4';

export interface RiskItem {
  id: string;
  companyName: string;
  vendorId?: string;
  location?: string;
  country?: string;
  level: RiskLevel;
  levelName: string; // e.g. "严重", "高风险", "中度", "轻微关注"
  riskType: string; // e.g. "被列入失信被执行人", "重大劳资纠纷诉讼", "环保处罚"
  summary: string;
  aiConfidence: number; // e.g. 99.8
  updatedTime: string;
  matchType?: string; // e.g. "语义提取", "实体识别", "关键词匹配"
  source?: string; // e.g. "Reuters", "Gov API", "Bloomberg"
  tags?: string[];
  status: 'valid' | 'invalid';
  
  // Detailed information
  overallScore?: number; // e.g. 85 or 92
  eventCategory?: string; // e.g. "供应链 - 产能下降"
  impactScope?: string; // e.g. "华东区交付延迟 2-4 周"
  
  // Evidence chain
  evidenceChain?: {
    sourceName: string;
    sourceType: string;
    eventSummary: string;
    matchStatus: string;
    ruleTriggered: string;
    calculatedScore: number;
  };
  
  timeline?: {
    time: string;
    stage: string;
    title: string;
    description: string;
    details?: Record<string, string>;
  }[];
  
  scoreBreakdown?: {
    category: string;
    score: number;
    maxScore: number;
    weightPercent: number;
    contribution: number;
  }[];
  
  originalSignals?: {
    title: string;
    source: string;
    time: string;
  }[];
  
  matchReasons?: {
    entityMatch: string;
    locationMatch: string;
    keywords: string[];
  };
}

export interface Supplier {
  id: string;
  code: string; // e.g. VND-88392
  legalName: string; // 法人主体
  registrationNo: string; // 注册号
  productionLocation: string; // 生产地点
  countryRegion?: string;
  tier: 'Tier 1' | 'Tier 2' | 'Tier 3';
  category: string; // e.g. 微电子元件, 特种溶剂
  suppliedProduct: string; // 供应产品
  monitoringStatus: 'normal' | 'high_risk' | 'paused'; // 正常监控 | 高危预警 | 暂停监控
  riskLevel?: RiskLevel;
  riskScore?: number;
  lastUpdated: string;
}

export interface DataSource {
  id: string;
  name: string; // e.g. 工商数据接口, 司法诉讼爬虫
  type: string;
  status: 'normal' | 'warning' | 'error';
  latency: string; // e.g. "正常运行", "延迟 2h"
  lastSyncTime: string;
  itemCount: number;
}

export interface MonitoringDimension {
  id: string;
  name: string; // e.g. 自然环境, 地缘政治, 法律合规, 财务健康
  icon: string;
  enabled: boolean;
  ruleId: string;
  severityWeight: number; // 0 - 1
  relevanceWeight: number; // 0 - 1
  thresholds: {
    p1: number; // >= 85
    p2: number; // >= 65
    p3: number; // >= 40
  };
  ttlHours: number; // e.g. 72
}

export interface RuleEngineConfig {
  dimensions: MonitoringDimension[];
}

export type ActiveTab =
  | 'overview'
  | 'current-risks'
  | 'risk-assistant'
  | 'suppliers'
  | 'data-sources'
  | 'rules';

export interface ToolCall {
  id: string;
  toolName: string; // e.g. "query_suppliers_database", "external_check_tianyancha"
  description: string; // e.g. "调用重点供应商核心库"
  params: Record<string, any>;
  durationMs: number;
  status: 'success' | 'warning' | 'failed';
  resultCount?: number;
}

export interface ExternalCompanyCheck {
  companyName: string;
  registrationNo: string;
  legalPerson: string;
  regCapital: string;
  establishedDate: string;
  operatingStatus: string; // e.g. "存续（在营、开业、在册）"
  riskCount: {
    judgements: number; // 司法诉讼
    abnormalOps: number; // 经营异常
    administrativePenalties: number; // 行政处罚
    dishonest: number; // 失信记录
  };
  keyBusiness: string;
  checkTime: string;
  source: '天眼查 API 实时核查';
  isExternal: boolean; // Always true for non-list enterprises
}

export interface TianYanChaQuota {
  dailyUsed: number;
  dailyLimit: number;
  monthlyUsed: number;
  monthlyLimit: number;
  lastResetTime: string;
  status: 'normal' | 'warning' | 'exceeded';
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant';
  timestamp: string;
  content: string;
  toolCalls?: ToolCall[];
  data?: {
    riskCards?: RiskItem[];
    supplierCards?: Supplier[];
    externalCheckCard?: ExternalCompanyCheck;
    quotaCard?: TianYanChaQuota;
  };
}
