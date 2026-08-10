export interface RiskTrendPoint {
  date: string;
  P1: number;
  P2: number;
  P3: number;
  P4: number;
  lawsuit: number;
  financial: number;
  sanction: number;
  disruption: number;
  avgRiskIndex: number;
}

export const mock30DayTrendData: RiskTrendPoint[] = [
  { date: '10/01', P1: 1, P2: 4, P3: 10, P4: 25, lawsuit: 12, financial: 8, sanction: 3, disruption: 17, avgRiskIndex: 42 },
  { date: '10/03', P1: 2, P2: 5, P3: 12, P4: 28, lawsuit: 14, financial: 9, sanction: 4, disruption: 20, avgRiskIndex: 45 },
  { date: '10/05', P1: 1, P2: 6, P3: 15, P4: 30, lawsuit: 15, financial: 11, sanction: 4, disruption: 22, avgRiskIndex: 48 },
  { date: '10/07', P1: 3, P2: 8, P3: 18, P4: 35, lawsuit: 18, financial: 14, sanction: 6, disruption: 26, avgRiskIndex: 54 },
  { date: '10/09', P1: 2, P2: 7, P3: 20, P4: 38, lawsuit: 20, financial: 12, sanction: 5, disruption: 30, avgRiskIndex: 52 },
  { date: '10/11', P1: 4, P2: 10, P3: 22, P4: 40, lawsuit: 24, financial: 16, sanction: 8, disruption: 28, avgRiskIndex: 61 },
  { date: '10/13', P1: 3, P2: 9, P3: 25, P4: 42, lawsuit: 22, financial: 15, sanction: 7, disruption: 35, avgRiskIndex: 59 },
  { date: '10/15', P1: 5, P2: 12, P3: 28, P4: 45, lawsuit: 28, financial: 19, sanction: 10, disruption: 33, avgRiskIndex: 68 },
  { date: '10/17', P1: 4, P2: 11, P3: 30, P4: 50, lawsuit: 26, financial: 18, sanction: 9, disruption: 42, avgRiskIndex: 65 },
  { date: '10/19', P1: 6, P2: 14, P3: 32, P4: 52, lawsuit: 32, financial: 22, sanction: 12, disruption: 38, avgRiskIndex: 74 },
  { date: '10/21', P1: 5, P2: 13, P3: 35, P4: 55, lawsuit: 30, financial: 20, sanction: 11, disruption: 44, avgRiskIndex: 72 },
  { date: '10/23', P1: 8, P2: 16, P3: 38, P4: 60, lawsuit: 36, financial: 25, sanction: 15, disruption: 46, avgRiskIndex: 81 },
  { date: '10/25', P1: 7, P2: 15, P3: 40, P4: 62, lawsuit: 34, financial: 23, sanction: 14, disruption: 53, avgRiskIndex: 79 },
  { date: '10/27', P1: 10, P2: 18, P3: 42, P4: 65, lawsuit: 40, financial: 28, sanction: 18, disruption: 49, avgRiskIndex: 87 },
  { date: '10/29', P1: 9, P2: 17, P3: 45, P4: 68, lawsuit: 38, financial: 26, sanction: 16, disruption: 59, avgRiskIndex: 84 },
  { date: '10/31', P1: 12, P2: 20, P3: 48, P4: 70, lawsuit: 42, financial: 30, sanction: 20, disruption: 58, avgRiskIndex: 89 },
];

export interface RiskCategoryPieData {
  name: string;
  value: number;
  count: number;
  color: string;
}

export const mockRiskCategoryData: RiskCategoryPieData[] = [
  { name: '司法诉讼与破产重组', value: 38, count: 184, color: '#007aff' },
  { name: '财务与税务异常', value: 26, count: 126, color: '#ff9500' },
  { name: '国际制裁与出口管制', value: 18, count: 87, color: '#ff3b30' },
  { name: '生产履约与劳资纠纷', value: 12, count: 58, color: '#af52de' },
  { name: '环保与监管处罚', value: 6, count: 29, color: '#34c759' },
];

export interface SupplierTierAnalysis {
  tier: string;
  totalCount: number;
  highRiskCount: number;
  avgRiskScore: number;
  topCategory: string;
}

export const mockSupplierTierData: SupplierTierAnalysis[] = [
  { tier: 'Tier 1 (核心一类)', totalCount: 248, highRiskCount: 14, avgRiskScore: 78, topCategory: '微电子 & 芯片设计' },
  { tier: 'Tier 2 (关键二类)', totalCount: 420, highRiskCount: 28, avgRiskScore: 62, topCategory: '特种材料 & 晶圆封装' },
  { tier: 'Tier 3 (一般三类)', totalCount: 580, highRiskCount: 44, avgRiskScore: 41, topCategory: '包装 & 通用五金' },
];

export interface DataSourcePerformance {
  source: string;
  latencyMs: number;
  dailyVolume: number;
  accuracy: number;
  status: 'optimal' | 'degraded' | 'normal';
}

export const mockDataSourcePerformance: DataSourcePerformance[] = [
  { source: '天眼查 API', latencyMs: 320, dailyVolume: 4592, accuracy: 99.4, status: 'degraded' },
  { source: '裁判文书网', latencyMs: 1800, dailyVolume: 1284, accuracy: 96.2, status: 'degraded' },
  { source: '海关与航运', latencyMs: 240, dailyVolume: 890, accuracy: 98.8, status: 'optimal' },
  { source: '彭博/路透', latencyMs: 45, dailyVolume: 15420, accuracy: 97.5, status: 'optimal' },
  { source: 'OFAC/BIS 制裁', latencyMs: 85, dailyVolume: 3210, accuracy: 99.9, status: 'optimal' },
];
