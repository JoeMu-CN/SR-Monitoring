import React from 'react';
import {RuleEngineMode} from './RuleEngineContext';

export interface RuleEngineExplainersProps {
  /** 由壳组件控制：观察态与配置态都可读（viewer 也可读） */
  mode: RuleEngineMode;
}

interface ExplainerEntry {
  title: string;
  body: string;
  example: string;
}

/**
 * 配置项语义说明层挂载点（组件归属 todo 10，本文件由 todo 4 先落基础文案）。
 * todo 10 将在本文件内补齐「当前值含义」、总分公式与两条封顶规则的完整说明。
 */
const EXPLAINERS: ExplainerEntry[] = [
  {
    title: '匹配柱',
    body: '事件与供应商建立关联时可用的证据通道；至少保留一柱，未启用的柱不参与匹配。',
    example: '只启用「主体」时，仅注册号/法人全称/别名命中的供应商会进入评分。',
  },
  {
    title: '事件类型',
    body: '每个事件类型只由一个启用维度接管；同一事件类型被两个维度声明时保存会被拒绝。',
    example: '「天气」由自然环境接管，「贸易政策」由经济贸易接管。',
  },
  {
    title: '严重程度分值',
    body: '事件本身的严重程度带来的基础分，越高代表事件越关键。',
    example: '严重 35 分、高 28 分、中 20 分、低 10 分。',
  },
  {
    title: '关联类型分值',
    body: '供应商与事件关联方式的强度分，精确匹配高于文本相近匹配。',
    example: '注册号精确匹配 30 分，高于国家 8 分。',
  },
  {
    title: '分级阈值',
    body: '总分换算为 P1/P2/P3 的分数线，必须满足 P1 阈值 > P2 阈值 > P3 阈值；总分 = 严重程度 + 关联强度 + 来源可信度 + 时效性 + 产品相关性（上限 100）。',
    example: '总分 85 分及以上为 P1；两条封顶规则：只命中「国家」柱最高 P4，未命中主体精确匹配最高 P2。',
  },
  {
    title: '信号过滤规则',
    body: '调用大模型之前的确定性粗筛，用于省钱和控制噪音，不改变最终评分。',
    example: '命中高影响关键词或重点关注国家自动放行；国外事件且当地无任何供应商会被直接过滤。',
  },
];

export const RuleEngineExplainers: React.FC<RuleEngineExplainersProps> = ({mode}) => {
  return (
    <section
      data-testid="rule-engine-explainers"
      data-mode={mode}
      className="space-y-3 rounded-2xl border border-slate-200/80 dark:border-slate-700/60 bg-white/80 dark:bg-slate-800/60 p-4 shadow-sm"
    >
      <div>
        <h2 className="font-bold text-[15px] text-[#101d28] dark:text-white">规则语义说明</h2>
        <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
          每个配置项的人话解释与例子，对照真实评分与过滤语义，只读可查。
        </p>
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
        {EXPLAINERS.map((entry) => (
          <article
            key={entry.title}
            className="min-w-0 rounded-xl border border-slate-200 dark:border-slate-700 bg-[#f7f9ff] dark:bg-slate-950/40 p-3"
          >
            <h3 className="text-[12px] font-bold text-[#424751] dark:text-slate-300">{entry.title}</h3>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-600 dark:text-slate-300">{entry.body}</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">例：{entry.example}</p>
          </article>
        ))}
      </div>
    </section>
  );
};
