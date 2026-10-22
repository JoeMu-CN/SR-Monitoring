import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {ValidityStateBadge} from './ValidityStateBadge';

afterEach(cleanup);

describe('ValidityStateBadge', () => {
  it('生命周期与处理状态可并存，并各自使用中文标签', () => {
    render(<ValidityStateBadge validityState="active" processingState="no_trigger" />);

    expect(screen.getByText('有效')).toBeInTheDocument();
    expect(screen.getByText('未触发（数据积累中）')).toBeInTheDocument();
  });

  it('生命周期为 null 时不渲染生命周期徽标', () => {
    render(<ValidityStateBadge validityState={null} processingState="filtered" />);

    expect(screen.queryByText('其他状态')).not.toBeInTheDocument();
    expect(screen.getByText('已过滤')).toBeInTheDocument();
  });

  it('analyzed 不单独展示处理状态徽标', () => {
    const {container} = render(<ValidityStateBadge validityState="active" processingState="analyzed" />);

    expect(screen.getByText('有效')).toBeInTheDocument();
    expect(screen.queryByText('已分析')).not.toBeInTheDocument();
    expect(container.textContent).toBe('有效');
  });

  it('两个状态都为空时不渲染任何徽标', () => {
    const {container} = render(<ValidityStateBadge validityState={null} processingState={null} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('历史 irrelevant 生命周期按「已过滤」展示', () => {
    render(<ValidityStateBadge validityState="irrelevant" />);

    expect(screen.getByText('已过滤')).toBeInTheDocument();
    expect(screen.queryByText('不相关')).not.toBeInTheDocument();
  });
});
