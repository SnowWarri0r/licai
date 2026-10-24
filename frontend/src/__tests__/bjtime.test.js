import { describe, expect, it } from 'vitest'
import { bjToUsTradeDate, usTradeToBjDate } from '../helpers'

describe('美股按北京时间录入 → 美东交易日', () => {
  it('北京晚上的成交就是当天的美东交易日', () => {
    expect(bjToUsTradeDate('2026-10-19', '22:35')).toBe('2026-10-19')
  })
  it('北京凌晨的成交归到前一个美东交易日', () => {
    expect(bjToUsTradeDate('2026-10-20', '02:10')).toBe('2026-10-19')
    expect(bjToUsTradeDate('2026-01-06', '04:30')).toBe('2026-01-05')   // 冬令时
  })
  it('不填时刻按北京日期当晚那场算', () => {
    expect(bjToUsTradeDate('2026-10-19', '')).toBe('2026-10-19')
  })
  it('反向换回北京日期, 与录入一致', () => {
    for (const [d, t] of [['2026-10-19', '22:35'], ['2026-10-20', '02:10'], ['2026-01-06', '04:30']]) {
      expect(usTradeToBjDate(bjToUsTradeDate(d, t), t)).toBe(d)
    }
    expect(usTradeToBjDate('2026-10-19', null)).toBe('2026-10-19')
  })
})
