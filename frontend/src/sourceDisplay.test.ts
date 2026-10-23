import {describe, expect, it} from 'vitest';
import {displaySourceName} from './sourceDisplay';

describe('displaySourceName 去括号展示', () => {
  it.each([
    ['统计局制造业 PMI（E1 宏观经济）', '统计局制造业 PMI'],
    ['Source (E1)', 'Source'],
    ['  Source   (E1)  ', 'Source'],
    ['生态环境部公告（P1 行业监管）(监管)', '生态环境部公告'],
    ['商务部实体控制清单（BIS）', '商务部实体控制清单'],
  ])('「%s」展示为「%s」', (raw, expected) => {
    expect(displaySourceName(raw)).toBe(expected);
  });

  it('括号前后多段空白压缩为单空格', () => {
    expect(displaySourceName('统计局（E1）   制造业 PMI')).toBe('统计局 制造业 PMI');
  });

  it('无括号时原样返回', () => {
    expect(displaySourceName('中央气象台')).toBe('中央气象台');
  });

  it('全为括号内容时回退原名，不产生空名称', () => {
    expect(displaySourceName('（E1 宏观经济）')).toBe('（E1 宏观经济）');
  });
});
