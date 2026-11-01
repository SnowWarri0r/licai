import { describe, expect, it } from 'vitest'
import { markFailed } from '../components/askShared'

const steps = [
  { tool: 'resolve_stock', arg: 'LLY' },
  { tool: 'resolve_stock', arg: 'TER' },
  { tool: 'resolve_stock', arg: 'ABSI' },
]

describe('取数失败回填到芯片', () => {
  it('同名工具并发时按参数对到失败的那一个', () => {
    const out = markFailed(steps, { tool: 'resolve_stock', arg: 'TER', err: '找不到 TER' })
    expect(out.map(s => !!s.failed)).toEqual([false, true, false])
  })
  it('老记录不带参数: 退回标最近一个同名芯片', () => {
    const out = markFailed(steps, { tool: 'resolve_stock', err: 'x' })
    expect(out.map(s => !!s.failed)).toEqual([false, false, true])
  })
})
