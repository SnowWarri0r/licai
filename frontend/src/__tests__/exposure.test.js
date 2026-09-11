import { describe, expect, it } from 'vitest'

import { buildFamilyIndex, riskFamiliesOf } from '../components/portfolio/helpers'
import { MIN_HOLDER_PCT } from '../components/portfolio/constants'

// 穿透同源: 后端把基金拆到季报前十大, 这里把"同一个行业里有几只在持"归成家族,
// 行上打 ↔同源 角标。门槛 MIN_HOLDER_PCT 是这块唯一的可调旋钮 ——
// 调低满屏噪音(债基前十大混进一只光模块 392 元也算同源), 调高漏掉真重叠。

const mkExpo = (total, industries) => ({ total, industries })

describe('buildFamilyIndex — 同源家族索引', () => {
  it('后端没回来时返回 null(而不是空对象), 调用方据此走关键词兜底', () => {
    expect(buildFamilyIndex(null)).toBeNull()
    expect(buildFamilyIndex(undefined)).toBeNull()
    expect(buildFamilyIndex({})).toBeNull()
    expect(buildFamilyIndex({ industries: '不是数组' })).toBeNull()
  })

  it('一个行业只有一个在持 → 不算家族(一个人构不成"同源")', () => {
    const idx = buildFamilyIndex(mkExpo(100000, [
      { industry: '贵金属', holders: [{ kind: 'A', code: '600547', mv: 50000 }] },
    ]))
    expect(idx.fams.size).toBe(0)
    expect(idx.byRow.size).toBe(0)
  })

  it('两个及以上才成家族, 家族市值按穿透后的持有额相加', () => {
    const idx = buildFamilyIndex(mkExpo(100000, [
      { industry: '贵金属', holders: [
        { kind: 'A', code: '600547', mv: 30000 },
        { kind: 'F', code: '518880', mv: 20000 },
      ] },
    ]))
    expect(idx.fams.get('贵金属').mv).toBe(50000)
    expect(idx.byRow.get('A:600547')).toEqual(['贵金属'])
    expect(idx.byRow.get('F:518880')).toEqual(['贵金属'])
  })

  it(`低于 ${MIN_HOLDER_PCT}% 的零头先被剔掉, 剔完不够两个就不成家族`, () => {
    // total=100000 → 门槛 500。一个 30000 + 一个 392(那只光模块)
    const idx = buildFamilyIndex(mkExpo(100000, [
      { industry: '光模块', holders: [
        { kind: 'A', code: '300308', mv: 30000 },
        { kind: 'F', code: '000001', mv: 392 },
      ] },
    ]))
    expect(idx.fams.size).toBe(0)   // 392 被剔 → 只剩 1 个 → 不成家族
  })

  it('门槛是"大于等于", 正好卡在线上的要留下', () => {
    const floor = 100000 * MIN_HOLDER_PCT / 100
    const idx = buildFamilyIndex(mkExpo(100000, [
      { industry: '贵金属', holders: [
        { kind: 'A', code: '600547', mv: 30000 },
        { kind: 'F', code: '518880', mv: floor },
      ] },
    ]))
    expect(idx.fams.has('贵金属')).toBe(true)
  })

  it('未知行业与海外行业不参与归族', () => {
    const idx = buildFamilyIndex(mkExpo(100000, [
      { industry: '未知行业', holders: [{ kind: 'A', code: 'a', mv: 9000 }, { kind: 'A', code: 'b', mv: 9000 }] },
      { industry: '海外科技', holders: [{ kind: 'A', code: 'c', mv: 9000 }, { kind: 'A', code: 'd', mv: 9000 }] },
      { industry: '', holders: [{ kind: 'A', code: 'e', mv: 9000 }, { kind: 'A', code: 'f', mv: 9000 }] },
    ]))
    expect(idx.fams.size).toBe(0)
  })

  it('一只票可以同属多个家族, codes 多代码(A/C 份额)各自建索引', () => {
    const idx = buildFamilyIndex(mkExpo(100000, [
      { industry: '贵金属', holders: [
        { kind: 'F', code: '主', codes: ['011", 无所谓', '012'], mv: 9000 },
        { kind: 'A', code: '600547', mv: 9000 },
      ] },
      { industry: '有色', holders: [
        { kind: 'A', code: '600547', mv: 9000 },
        { kind: 'A', code: '000630', mv: 9000 },
      ] },
    ]))
    expect(idx.byRow.get('A:600547')).toEqual(['贵金属', '有色'])
    // codes 有值时按 codes 展开, 不再用 code
    expect(idx.byRow.get('F:012')).toEqual(['贵金属'])
    expect(idx.byRow.has('F:主')).toBe(false)
  })
})

describe('riskFamiliesOf — 后端没回来时的关键词兜底', () => {
  it('白银同时进 silver 和 metals(它确实是两块风险)', () => {
    expect(riskFamiliesOf({ type: 'A', name: '盛达资源白银' })).toEqual(['silver', 'metals'])
  })

  it('黄金只进 gold, 不进 metals', () => {
    // 分开是有意的: 金价跟工业金属的驱动不是一回事
    expect(riskFamiliesOf({ type: 'A', name: '山东黄金' })).toEqual(['gold'])
  })

  it('工业金属关键词进 metals', () => {
    expect(riskFamiliesOf({ type: 'A', name: '铜陵有色' })).toEqual(['metals'])
    expect(riskFamiliesOf({ type: 'A', name: '中钨高新' })).toEqual([])  // 钨不在词表里
  })

  it('名字里没有金属字样的就认不出 —— 这正是要靠后端穿透的原因', () => {
    // 「兴业银锡」实际是贵金属, 但名字里一个金属字都没有(银锡 ≠ 白银)
    expect(riskFamiliesOf({ type: 'A', name: '兴业银锡' })).toEqual([])
  })

  it('基金按已归好的 category.id 映射', () => {
    expect(riskFamiliesOf({ type: 'F', category: { id: 'silver' } })).toEqual(['silver', 'metals'])
    expect(riskFamiliesOf({ type: 'F', category: { id: 'gold' } })).toEqual(['gold'])
    expect(riskFamiliesOf({ type: 'F', category: { id: 'overseas' } })).toEqual(['overseas'])
    expect(riskFamiliesOf({ type: 'F', category: { id: 'aindex' } })).toEqual(['cn_broad'])
    expect(riskFamiliesOf({ type: 'F', category: { id: 'other' } })).toEqual([])
    expect(riskFamiliesOf({ type: 'F' })).toEqual([])
  })

  it('现金/理财/加密/机器人不参与同源', () => {
    for (const t of ['M', 'W', 'C', 'R']) {
      expect(riskFamiliesOf({ type: t, name: '黄金' })).toEqual([])
    }
  })
})
