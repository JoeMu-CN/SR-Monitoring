import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter, Route, Routes, useNavigate} from 'react-router-dom';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {EventDetailRead, RiskAlertRead} from '../api';
import {RiskRouteView} from '../RiskRouteView';

interface MockResponse {
  readonly ok: boolean;
  readonly status: number;
  json: () => Promise<unknown>;
}

const response = (body: unknown, status = 200): MockResponse => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

const alert = (id: number, status: RiskAlertRead['status'] = 'current'): RiskAlertRead => ({
  id,
  level: 'P2',
  score: 72,
  score_detail: {severity: 30, association: 22, rule_version: 'v1'},
  status,
  supplier_id: 12,
  supplier_name: `供应商 ${id}`,
  event_id: id * 10,
  event_type: 'logistics',
  event_subtype: 'transport_disruption',
  event_summary: `事件摘要 ${id}`,
  event_start_at: '2026-08-30T08:00:00Z',
  event_end_at: null,
  confidence: 0.91,
  match_type: 'entity',
  match_reasons: ['主体名称匹配'],
  match_evidence: [{supplier_name: `供应商 ${id}`}],
  source_title: `来源 ${id}`,
  source_url: `https://example.test/source-${id}`,
  published_at: '2026-08-30T08:00:00Z',
  updated_at: '2026-08-31T08:00:00Z',
  expires_at: null,
  expiry_kind: 'none',
  validity_state: 'active',
  valid_until: null,
  review_due_at: null,
  validity_policy_version: 'v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
});

const event = (id: number, overrides: Partial<EventDetailRead> = {}): EventDetailRead => ({
  id,
  dedup_key: `dedup-${id}`,
  event_type: 'logistics',
  event_subtype: 'transport_disruption',
  severity: 'high',
  summary: `事件详情 ${id}`,
  start_at: '2026-08-30T08:00:00Z',
  end_at: null,
  confidence: 0.88,
  created_at: '2026-08-30T08:00:00Z',
  signals: [{signal_id: id, title: `原始信号 ${id}`, content: '信号原文内容', url: 'https://example.test/signal', published_at: '2026-08-30T08:00:00Z', collected_at: '2026-08-30T09:00:00Z', validity_state: 'active'}],
  entities: [{name: '关联主体', normalized_name: '关联主体有限公司', registry_no: '91310000'}],
  locations: [{name: '上海生产地点', country_code: 'CN', region: '上海', city: '上海', district: '浦东', latitude: 31.2, longitude: 121.5, radius_km: 10}],
  validity_state: 'active',
  valid_until: null,
  review_due_at: null,
  validity_policy_version: 'v1',
  validity_reason: {code: 'active', anchor_source: 'published_at', details: {}},
  ...overrides,
});

const renderAt = (path: string, onRequestError = vi.fn()) => render(
  <MemoryRouter initialEntries={[path]}>
    <Routes>
      <Route path="/risks/:alertId" element={<RiskRouteView riskItems={[]} onAskAssistant={vi.fn()} onCloseDetail={vi.fn()} onExportReport={vi.fn()} onSelectRisk={vi.fn()} onRequestError={onRequestError} />} />
    </Routes>
  </MemoryRouter>,
);

const RouteSwitch = () => {
  const navigate = useNavigate();
  return (
    <>
      <button type="button" onClick={() => navigate('/risks/2')}>切换提醒</button>
      <Routes>
        <Route path="/risks/:alertId" element={<RiskRouteView riskItems={[]} onAskAssistant={vi.fn()} onCloseDetail={vi.fn()} onExportReport={vi.fn()} onSelectRisk={vi.fn()} onRequestError={vi.fn()} />} />
      </Routes>
    </>
  );
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('RiskDetailView', () => {
  it('先加载提醒再加载事件，并展示 API 返回的证据', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText('原始信号 70')).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/v1/risk-alerts/7', '/api/v1/events/70']);
    expect(screen.getByText(/规范名称：关联主体有限公司/)).toBeInTheDocument();
    expect(screen.getByText('上海生产地点')).toBeInTheDocument();
  });

  it.each([
    ['current', '当前有效'],
    ['expired', '已失效'],
  ] as const)('渲染 %s 提醒状态', async (status, statusLabel) => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7, status)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText(statusLabel)).toBeInTheDocument();
  });

  it('关键证据缺失时显示明确空态，可选主体与地点分组不占空卡', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {signals: [], entities: [], locations: []})));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText('暂无原始信号')).toBeInTheDocument();
    expect(screen.queryByRole('heading', {name: '关联主体'})).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', {name: '关联地点'})).not.toBeInTheDocument();
  });

  it('无匹配理由与匹配证据时提示缺失，有值时逐条展示', async () => {
    const withoutEvidence = vi.fn()
      .mockResolvedValueOnce(response({...alert(7), match_reasons: [], match_evidence: []}))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', withoutEvidence);

    renderAt('/risks/7');
    expect(await screen.findByText('暂无匹配理由')).toBeInTheDocument();
    expect(screen.getByText('暂无匹配证据')).toBeInTheDocument();
    cleanup();

    const withEvidence = vi.fn()
      .mockResolvedValueOnce(response({
        ...alert(7),
        match_reasons: ['法人全称精确匹配：上海电气股份有限公司'],
        match_evidence: [{object_type: 'supplier', supplier_id: 12, legal_name: '上海电气股份有限公司', unknown_field: '保留原文'}],
      }))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', withEvidence);

    renderAt('/risks/7');
    expect(await screen.findByText('法人全称精确匹配：上海电气股份有限公司')).toBeInTheDocument();
    const evidence = screen.getByText('法人全称：').closest('li');
    expect(evidence).not.toBeNull();
    // 常见键本地中文化，未知键保留原文，不丢数据
    expect(evidence).toHaveTextContent('对象类型：供应商');
    expect(evidence).toHaveTextContent('unknown_field：保留原文');
  });

  it('标题与正文归一化空白后相同时只呈现一次，不同则正文完整保留换行', async () => {
    const sameText = '中央气象台发布暴雨橙色预警';
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {
        event_type: 'weather',
        event_subtype: 'weather_alert',
        severity: 'critical',
        signals: [
          {signal_id: 1, title: sameText, content: `  ${sameText}  `, url: null, published_at: '2026-08-30T08:00:00Z', collected_at: '2026-08-30T09:00:00Z', validity_state: 'active'},
          {signal_id: 2, title: '台风路径通报', content: '第一行正文\n第二行正文', url: null, published_at: null, collected_at: '2026-08-30T09:00:00Z', validity_state: 'expired'},
        ],
      })));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findAllByText(sameText)).toHaveLength(1);
    // 事件类型/严重性走局部中文映射
    expect(screen.getByText('天气预警')).toBeInTheDocument();
    expect(screen.getByText('重大')).toBeInTheDocument();
    const detail = screen.getByText((_, node) => node?.textContent === '第一行正文\n第二行正文');
    expect(detail).toBeInTheDocument();
    expect(detail).toHaveClass('whitespace-pre-wrap');
  });

  it('来源与多个信号各自使用清晰链接文案，不铺满完整 URL', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {
        signals: [
          {signal_id: 1, title: '信号一标题', content: '信号一正文内容不同', url: 'https://example.test/first', published_at: '2026-08-30T08:00:00Z', collected_at: '2026-08-30T09:00:00Z', validity_state: 'active'},
          {signal_id: 2, title: '信号二标题', content: '信号二正文内容不同', url: 'https://example.test/second', published_at: '2026-08-30T08:00:00Z', collected_at: '2026-08-30T09:00:00Z', validity_state: 'active'},
        ],
      })));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    const sourceLink = await screen.findByRole('link', {name: '查看来源原文'});
    expect(sourceLink).toHaveAttribute('href', 'https://example.test/source-7');
    expect(sourceLink).not.toHaveTextContent('example.test');
    const signalLinks = screen.getAllByRole('link', {name: '查看信号原文'});
    expect(signalLinks.map((link) => link.getAttribute('href'))).toEqual(['https://example.test/first', 'https://example.test/second']);
  });

  it('无坐标无半径时隐藏该行，有值时完整保留', async () => {
    const withoutMetrics = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {locations: [{name: '仅有地名的地点', country_code: 'CN', region: null, city: null, district: null, latitude: null, longitude: null, radius_km: null}]})));
    vi.stubGlobal('fetch', withoutMetrics);

    const {container: firstRender} = renderAt('/risks/7');
    expect(await screen.findByText('仅有地名的地点')).toBeInTheDocument();
    expect(firstRender.textContent).not.toContain('坐标：');
    cleanup();

    const withMetrics = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {locations: [{name: '带坐标的地点', country_code: 'CN', region: '上海', city: '上海', district: '浦东', latitude: 31.2, longitude: 121.5, radius_km: 25}]})));
    vi.stubGlobal('fetch', withMetrics);

    renderAt('/risks/7');
    expect(await screen.findByText('坐标：31.2，121.5；范围：25 km')).toBeInTheDocument();
  });

  it.each([
    ['仅纬度且为 0', {latitude: 0, longitude: null, radius_km: null}, '纬度：0'],
    ['仅经度', {latitude: null, longitude: 121.5, radius_km: null}, '经度：121.5'],
    ['仅半径独立展示', {latitude: null, longitude: null, radius_km: 40}, '范围：40 km'],
  ] as const)('%s 时保留已有值不丢弃', async (_label, metrics, expected) => {
    const partial = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70, {locations: [{name: '部分坐标地点', country_code: 'CN', region: null, city: null, district: null, ...metrics}]})));
    vi.stubGlobal('fetch', partial);

    renderAt('/risks/7');

    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it('评分使用紧凑定义行，长文与强制规则整宽，未知键兜底保留', async () => {
    const richScore = vi.fn()
      .mockResolvedValueOnce(response({
        ...alert(7),
        score_detail: {
          final_level: 'P1',
          capped_level: 'P2',
          level_cap: 'weak_association_max_p2',
          severity: 40,
          association: 30,
          source_credibility: 16,
          timeliness: 10,
          product_relevance: 5,
          llm_adopted: true,
          llm_confidence: 0.82,
          llm_theta: 0.7,
          llm_rationale: '模型认为该事件直接命中主体制裁名单，采纳建议等级并提升优先级。',
          forced_rule: {name: '主体制裁强制规则', description: '主体直接命中制裁名单', reason: 'registry_no 精确匹配', original_level: 'P2', original_score: 55},
          dimension: 'corporate',
          rule_version: 'v1',
          future_unknown_key: '未来键值',
        },
      }))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', richScore);

    const {container} = renderAt('/risks/7');

    expect(await screen.findByText('最终等级')).toBeInTheDocument();
    // 长文与强制规则占满整行
    const rationaleRow = screen.getByText('LLM 采纳理由').closest('div');
    expect(rationaleRow?.className).toContain('lg:col-span-3');
    const forcedRow = screen.getByText('强制规则', {selector: 'dt'}).closest('div');
    expect(forcedRow?.className).toContain('lg:col-span-3');
    expect(forcedRow).toHaveTextContent('规则名称：主体制裁强制规则');
    expect(forcedRow).toHaveTextContent('原始等级：P2');
    expect(forcedRow).toHaveTextContent('原始分数：55');
    // 维度与封顶原因走局部映射，未知键保留原文
    expect(screen.getByText('供应商主体')).toBeInTheDocument();
    expect(screen.getByText('弱关联，最高 P2')).toBeInTheDocument();
    expect(screen.getByText('其他')).toBeInTheDocument();
    expect(screen.getByText('future_unknown_key')).toBeInTheDocument();
    expect(screen.getByText('未来键值')).toBeInTheDocument();
    // 不添加无口径百分比条
    expect(container.querySelector('progress')).toBeNull();
  });

  it('技术明细默认收起并保留完整契约字段', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    const {container} = renderAt('/risks/7');

    const details = await waitFor(() => {
      const found = container.querySelector('details');
      expect(found).not.toBeNull();
      return found as HTMLDetailsElement;
    });
    expect(details.open).toBe(false);
    const summary = container.querySelector('summary');
    expect(summary).toHaveTextContent('技术明细');
    // 保留原生 marker 作为展开指示，不隐藏它，也不用 aria-expanded 伪造状态
    expect(summary?.className).not.toContain('list-none');
    expect(summary?.getAttribute('aria-expanded')).toBeNull();
    // 主卡已移走的有效期原因与版本在技术明细中完整保留
    expect(screen.getByText('dedup-70')).toBeInTheDocument();
    expect(screen.getByText('提醒有效期原因')).toBeInTheDocument();
    expect(screen.getByText('事件有效期原因')).toBeInTheDocument();
    expect(screen.getByText('提醒评分规则版本')).toBeInTheDocument();
    expect(screen.getAllByText('v1').length).toBeGreaterThan(0);
  });

  it('点击 summary 展开技术明细并读出契约字段', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    const {container} = renderAt('/risks/7');

    const summary = await screen.findByText('技术明细');
    const details = summary.closest('details') as HTMLDetailsElement;
    expect(details.open).toBe(false);
    await user.click(summary);
    expect(details.open).toBe(true);
    expect(container.querySelector('details')?.querySelector('dl')).not.toBeNull();
    expect(screen.getByText('事件去重键').nextElementSibling?.textContent).toBe('dedup-70');
  });

  it('窄屏 DOM 顺序为概览 → 事件原始证据 → 供应商关联 → 评分 → 技术明细', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByRole('heading', {name: '供应商 7'})).toBeInTheDocument();
    const headings = screen.getAllByRole('heading').map((node) => node.textContent?.trim() ?? '');
    const order = ['供应商 7', '事件与来源', '原始信号', '供应商关联', '关联主体', '关联地点', '规则评分'];
    const positions = order.map((name) => headings.indexOf(name));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((left, right) => left - right));
    const scoreHeading = screen.getByRole('heading', {name: '规则评分'});
    expect(screen.getByText('技术明细').compareDocumentPosition(scoreHeading) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('提醒 404 时显示可返回列表的状态', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(response({detail: '风险提醒不存在'}, 404));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/404');

    expect(await screen.findByText('风险提醒不存在')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '返回风险列表'})).toBeInTheDocument();
  });

  it('提醒请求返回 401 时转交全局鉴权错误处理', async () => {
    const onRequestError = vi.fn();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({detail: '登录已失效'}, 401)));

    renderAt('/risks/7', onRequestError);

    expect(await screen.findByText('风险提醒加载失败')).toBeInTheDocument();
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 401}));
  });

  it('事件请求返回 403 时转交全局权限错误处理', async () => {
    const onRequestError = vi.fn();
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response({detail: '无权查看事件证据'}, 403)));

    renderAt('/risks/7', onRequestError);

    expect(await screen.findByRole('alert')).toHaveTextContent('事件详情加载失败');
    expect(onRequestError).toHaveBeenCalledWith(expect.objectContaining({status: 403}));
  });

  it('事件详情失败后只重试事件请求', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(alert(7)))
      .mockResolvedValueOnce(response({detail: '事件不存在'}, 404))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByRole('alert')).toHaveTextContent('事件详情加载失败');
    await user.click(screen.getByRole('button', {name: '重试事件详情'}));
    expect(await screen.findByText('原始信号 70')).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/v1/risk-alerts/7', '/api/v1/events/70', '/api/v1/events/70']);
  });

  it('加载中显示提醒详情状态：sr-only 提示仍在、不再渲染旋转图标', () => {
    const fetchMock = vi.fn(() => new Promise<MockResponse>(() => undefined));
    vi.stubGlobal('fetch', fetchMock);

    const {container} = renderAt('/risks/7');

    expect(screen.getByText('正在加载风险提醒详情')).toBeInTheDocument();
    expect(container.querySelector('.animate-spin')).toBeNull();
  });

  it('规则评分明细使用中文标题，并把同类等级条目分组相邻', async () => {
    const withLevels: RiskAlertRead = {
      ...alert(7),
      score_detail: {
        severity: 30, association: 22, final_level: 'P1', capped_level: 'P2',
        deterministic_level: 'P2', llm_level: null, rule_version: 'v1',
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(withLevels))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText('最终等级')).toBeInTheDocument();
    expect(screen.getByText('事件严重程度')).toBeInTheDocument();
    expect(screen.getByText('规则版本')).toBeInTheDocument();
    expect(screen.queryByText('final_level')).not.toBeInTheDocument();

    // 同类等级卡片相邻：均落在「等级判定」分组内
    const levelGroup = screen.getByText('等级判定').closest('div');
    expect(levelGroup).not.toBeNull();
    expect(levelGroup).toHaveTextContent('最终等级');
    expect(levelGroup).toHaveTextContent('封顶后等级');
    expect(levelGroup).toHaveTextContent('确定性基线等级');
    expect(levelGroup).toHaveTextContent('LLM 建议等级');

    // 四个等级按后端 resolve_level 的真实执行顺序排列（与 JSONB 键顺序无关）
    const levelOrder = ['确定性基线等级', 'LLM 建议等级', '封顶后等级', '最终等级'];
    const levelLabels = Array.from(levelGroup?.querySelectorAll('dt') ?? []).map((node) => node.textContent ?? '');
    const positions = levelOrder.map((name) => levelLabels.indexOf(name));
    expect(positions.every((position) => position >= 0)).toBe(true);
    expect(positions).toEqual([...positions].sort((left, right) => left - right));

    // 等级组固定四列同行：滚动容器在外、内层 dl 承 min-width，标题 nowrap 防孤字
    const levelList = levelGroup?.querySelector('dl');
    expect(levelList?.className).toContain('grid-cols-4');
    expect(levelList?.className).toContain('min-w-[22rem]');
    expect(levelList?.parentElement?.className).toContain('overflow-x-auto');
    for (const item of Array.from(levelList?.children ?? [])) {
      expect(item.querySelector('dt')?.className).toContain('whitespace-nowrap');
    }
  });

  it('等级判定四项同行，level_cap 与强制规则整行另起且不丢字段', async () => {
    const withAux: RiskAlertRead = {
      ...alert(7),
      score_detail: {
        // 故意打乱后端写入顺序，验证前端按执行顺序展示而非按键顺序
        final_level: 'P1',
        level_cap: 'country_only_max_p4',
        llm_level: 'P2',
        capped_level: 'P2',
        deterministic_level: 'P3',
        severity: 40,
        forced_rule: {name: '主体制裁强制规则', reason: 'registry_no 精确匹配'},
        future_unknown_key: '未来键值',
      },
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(withAux))
      .mockResolvedValueOnce(response(event(70)));
    vi.stubGlobal('fetch', fetchMock);

    renderAt('/risks/7');

    expect(await screen.findByText('等级判定')).toBeInTheDocument();
    const levelGroup = screen.getByText('等级判定').closest('div');

    // 四个等级同处一个四列 grid，且每项都只是一格，不被 span 成整行
    const items = Array.from(levelGroup?.querySelectorAll('dl > div') ?? []);
    const fourLevels = ['确定性基线等级', 'LLM 建议等级', '封顶后等级', '最终等级'];
    for (const name of fourLevels) {
      const node = items.find((item) => item.textContent?.startsWith(name));
      expect(node).not.toBeUndefined();
      expect(node?.className).not.toContain('col-span');
      expect(node?.className).toContain('min-w-0');
    }
    expect(items.length).toBe(5); // 四等级 + level_cap 辅助行

    // 辅助说明整宽另起一行，内容不丢
    const capRow = items.find((item) => item.textContent?.startsWith('等级封顶原因'));
    expect(capRow?.className).toContain('col-span-4');
    expect(capRow).toHaveTextContent('仅命中国家，最高 P4');

    const forcedRow = screen.getByText('强制规则', {selector: 'dt'}).closest('div');
    expect(forcedRow?.className).toContain('lg:col-span-3');
    expect(forcedRow).toHaveTextContent('规则名称：主体制裁强制规则');
    expect(forcedRow).toHaveTextContent('registry_no 精确匹配');

    // 未知键兜底组同样参与组间分隔，不被丢弃
    expect(screen.getByText('其他')).toBeInTheDocument();
    expect(screen.getByText('future_unknown_key')).toBeInTheDocument();
    expect(screen.getByText('未来键值')).toBeInTheDocument();
  });

  it('快速切换 alertId 时忽略旧提醒响应', async () => {
    let resolveFirstAlert: ((value: MockResponse) => void) | undefined;
    const firstAlert = new Promise<MockResponse>((resolve) => {
      resolveFirstAlert = resolve;
    });
    const fetchMock = vi.fn((path: string) => {
      if (path === '/api/v1/risk-alerts/1') return firstAlert;
      if (path === '/api/v1/risk-alerts/2') return Promise.resolve(response(alert(2)));
      return Promise.resolve(response(event(20)));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={['/risks/1']}>
        <RouteSwitch />
      </MemoryRouter>,
    );
    await user.click(screen.getByRole('button', {name: '切换提醒'}));
    expect(await screen.findByRole('heading', {name: '供应商 2'})).toBeInTheDocument();
    resolveFirstAlert?.(response(alert(1)));

    expect(screen.queryByText('供应商 1')).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual(['/api/v1/risk-alerts/1', '/api/v1/risk-alerts/2', '/api/v1/events/20']);
  });
});
