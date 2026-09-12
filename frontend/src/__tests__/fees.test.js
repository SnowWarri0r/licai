import { describe, expect, it } from 'vitest'

import { estimateFee, resolveFee } from '../helpers'

// 券商费率固件。真实结构来自 /api/brokers, 这里只放 resolveFee 会读的四个字段。
const BROKERS = [
  { name: '招商证券', is_default: true, stock_rate: 0.0001854, stock_min: 5, etf_rate: 0.0001, etf_min: 0.1 },
  { name: '银河证券', is_default: false, stock_rate: 0.00025, stock_min: 5, etf_rate: 0.0002, etf_min: 5 },
]

describe('resolveFee — 挑哪一档费率', () => {
  it('按名字命中指定券商', () => {
    expect(resolveFee(BROKERS, '银河证券', 'stock')).toEqual({ rate: 0.00025, min: 5 })
  })

  it('kind=etf 走 ETF 那一档, 不是股票那一档', () => {
    // 招商 ETF 最低 0.1 元 vs 股票最低 5 元 —— 拿错档位会让一笔小额 ETF 贵 50 倍
    expect(resolveFee(BROKERS, '招商证券', 'etf')).toEqual({ rate: 0.0001, min: 0.1 })
    expect(resolveFee(BROKERS, '招商证券', 'stock')).toEqual({ rate: 0.0001854, min: 5 })
  })

  it('名字给了但券商表里没有 → 回落到默认券商(不是报错, 也不是内置兜底)', () => {
    expect(resolveFee(BROKERS, '不存在证券', 'stock')).toEqual({ rate: 0.0001854, min: 5 })
  })

  it('没给名字 → 默认券商', () => {
    expect(resolveFee(BROKERS, null, 'stock')).toEqual({ rate: 0.0001854, min: 5 })
  })

  it('没有默认券商 → 用列表第一个', () => {
    const noDefault = BROKERS.map(b => ({ ...b, is_default: false }))
    expect(resolveFee(noDefault, null, 'stock')).toEqual({ rate: 0.0001854, min: 5 })
  })

  it('券商表为空/未加载 → 内置兜底 万1.854 + 5 元', () => {
    // 页面刚打开、/api/brokers 还没回来时走这条。给 0 费率会让预览显示"不要钱"
    expect(resolveFee([], null, 'stock')).toEqual({ rate: 0.0001854, min: 5 })
    expect(resolveFee(null, null, 'stock')).toEqual({ rate: 0.0001854, min: 5 })
  })
})

describe('estimateFee — 佣金取 max(按比例, 最低)', () => {
  it('大额按比例收', () => {
    // 10 万 × 万2.5 = 25 元 > 最低 5 元
    expect(estimateFee(100000, BROKERS, '银河证券', 'stock')).toBeCloseTo(25, 6)
  })

  it('小额触底到最低佣金', () => {
    // 1000 × 万2.5 = 0.25 元 → 收 5 元。这条是"最低 5 元"的全部意义所在
    expect(estimateFee(1000, BROKERS, '银河证券', 'stock')).toBe(5)
  })

  it('恰好等于门槛的那一笔不多收', () => {
    // 20000 × 万2.5 = 5.00, 与最低持平
    expect(estimateFee(20000, BROKERS, '银河证券', 'stock')).toBeCloseTo(5, 6)
    // 再多一块钱就按比例走了
    expect(estimateFee(20001, BROKERS, '银河证券', 'stock')).toBeGreaterThan(5)
  })

  it('场内 ETF 的低门槛不会被股票的 5 元顶掉', () => {
    // 招商 ETF: 1000 × 万1 = 0.1 元, 正好等于 etf_min
    expect(estimateFee(1000, BROKERS, '招商证券', 'etf')).toBeCloseTo(0.1, 6)
    // 同一笔钱走股票档会被顶到 5 元 —— 两者必须不同, 否则说明 kind 没生效
    expect(estimateFee(1000, BROKERS, '招商证券', 'stock')).toBe(5)
  })

  it('金额缺失当 0 算, 但仍收最低佣金', () => {
    // amount ?? 0 之后 max(0, min) = min。不是 0 —— 券商不会因为你填空就免费
    expect(estimateFee(null, BROKERS, '招商证券', 'stock')).toBe(5)
    expect(estimateFee(undefined, BROKERS, '招商证券', 'stock')).toBe(5)
    expect(estimateFee(0, BROKERS, '招商证券', 'stock')).toBe(5)
  })
})
