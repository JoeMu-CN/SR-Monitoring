import {cleanup, render, screen, within} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {SystemSplashScreen} from './SystemSplashScreen';

afterEach(cleanup);

describe('SystemSplashScreen', () => {
  it('根元素具备 role=status 与可访问名称，并渲染 logo 与标题', () => {
    render(<SystemSplashScreen />);
    const splash = screen.getByRole('status', {name: '正在初始化供应商风险监控平台'});

    expect(within(splash).getByAltText('SR Monitoring')).toBeInTheDocument();
    expect(within(splash).getByRole('heading', {name: '供应商风险智能监控平台'})).toBeInTheDocument();
  });

  it('渲染一条不确定态的加载进度条', () => {
    render(<SystemSplashScreen />);
    const splash = screen.getByRole('status', {name: '正在初始化供应商风险监控平台'});
    const progressbar = within(splash).getByRole('progressbar', {name: '正在加载'});

    expect(progressbar).toBeInTheDocument();
    // 不确定态：不展示百分比数字，也不暴露 aria-valuenow。
    expect(progressbar).not.toHaveAttribute('aria-valuenow');
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('不再展示任何自检项、旧假在线文案与加载指示', () => {
    render(<SystemSplashScreen />);

    for (const removed of [
      '系统状态自检',
      '数据库连接',
      '调度器心跳',
      '信息源状态',
      'AI 引擎',
      '正在加载…',
      'Gemini AI 在线',
      '天眼查 API 直连',
      '加密通信中',
      '节点在线',
    ]) {
      expect(screen.queryByText(removed)).not.toBeInTheDocument();
    }

    expect(screen.queryByTestId('self-check-percent')).not.toBeInTheDocument();
    expect(screen.queryByTestId('splash-loading-indicator')).not.toBeInTheDocument();
    for (const stateLabel of ['检查中', '正常', '告警', '错误', '不可用']) {
      expect(screen.queryByRole('img', {name: stateLabel})).not.toBeInTheDocument();
    }
  });
});
