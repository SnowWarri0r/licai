import { describe, expect, it } from 'vitest'

import { fundPassthroughBucketDetailed, fundPassthroughType, isOnchainEtf } from '../helpers'

// 这两个穿透函数决定"我的黄金 ETF 算不算进股票集中度"。判错的后果不是报错,
// 是看板上一句错的风险提示 —— 所以主要测**优先级**: 规则是顺序匹配, 先中先赢。

describe('fundPassthroughType — 6 桶粗穿透', () => {
  it('现金类最先匹配', () => {
    expect(fundPassthroughType('天弘余额宝货币')).toBe('M')
    expect(fundPassthroughType('现金管理类产品')).toBe('M')
  })

  it('债券类 → W(跟理财同档)', () => {
    expect(fundPassthroughType('易方达中短债债券A')).toBe('W')
    expect(fundPassthroughType('稳健增利')).toBe('W')
  })

  it('商品优先于海外 —— 黄金 QDII 算商品, 不算股票', () => {
    // 名字同时含「黄金」(规则3) 与 QDII(规则4), 顺序决定结果。
    // 反过来的话, 一个纯商品仓会被算进权益, 集中度提示就错了。
    expect(fundPassthroughType('汇添富黄金及贵金属QDII')).toBe('C')
    expect(fundPassthroughType('华夏黄金ETF联接C')).toBe('C')
    expect(fundPassthroughType('国投白银期货')).toBe('C')
  })

  it('海外权益 → A(这个粗口径不分 H/U, 全堆 A 桶)', () => {
    expect(fundPassthroughType('易方达全球成长精选混合(QDII)人民币C')).toBe('A')
    expect(fundPassthroughType('大成纳斯达克100ETF联接')).toBe('A')
    expect(fundPassthroughType('恒生科技指数')).toBe('A')
  })

  it('A股宽基/行业 → A', () => {
    expect(fundPassthroughType('沪深300ETF')).toBe('A')
    expect(fundPassthroughType('半导体ETF')).toBe('A')
  })

  it('认不出来的留在基金兜底桶, 不硬塞进权益', () => {
    expect(fundPassthroughType('某某稳赢一号')).toBe('F')
    expect(fundPassthroughType('')).toBe('F')
  })
})

describe('fundPassthroughBucketDetailed — 10 桶细穿透(配置建议用)', () => {
  it('港股优先于美股: 恒生 QDII 归 H 不归 U', () => {
    // 规则顺序是 H 在 U 前面。颠倒的话港股仓会被算成美股敞口
    expect(fundPassthroughBucketDetailed('华夏恒生科技ETF(QDII)')).toBe('H')
    expect(fundPassthroughBucketDetailed('中概互联网ETF')).toBe('H')
  })

  it('美股 → U', () => {
    expect(fundPassthroughBucketDetailed('大成纳斯达克100ETF联接A')).toBe('U')
    expect(fundPassthroughBucketDetailed('摩根标普500指数(QDII)人民币A')).toBe('U')
  })

  it('日欧越印 → OS', () => {
    expect(fundPassthroughBucketDetailed('华安日经225ETF')).toBe('OS')
    expect(fundPassthroughBucketDetailed('天弘越南市场')).toBe('OS')
  })

  it('债基单列 BND, 不跟理财混在一起', () => {
    // 粗口径把债券归 W(理财), 细口径要分开 —— 两个函数在这一点上故意不同
    expect(fundPassthroughType('易方达信用债')).toBe('W')
    expect(fundPassthroughBucketDetailed('易方达信用债')).toBe('BND')
  })

  it('「海外债」不被当成海外权益(负向前瞻 海外(?!债))', () => {
    // 去掉规则里那个 (?!债), 这只会被判成 OS —— 一个固收仓被算进海外权益敞口。
    // 注意它落的是兜底桶 F 而不是 BND: 「海外债」三个字里没有 债券/国债/信用债
    // 这些 BND 认的词。这是刻意的 —— 宁可留在兜底, 也不猜。
    expect(fundPassthroughBucketDetailed('某海外债基金')).toBe('F')
    expect(fundPassthroughBucketDetailed('某海外债基金')).not.toBe('OS')
    // 名字规范的海外债基就能正确落进 BND
    expect(fundPassthroughBucketDetailed('某海外债券基金')).toBe('BND')
  })
})

describe('isOnchainEtf — 场内/场外判定', () => {
  it('沪市 5xxxxx / 深市 1xxxxx 是场内', () => {
    expect(isOnchainEtf('510300')).toBe(true)   // 沪深300ETF
    expect(isOnchainEtf('588000')).toBe(true)   // 科创50ETF
    expect(isOnchainEtf('159915')).toBe(true)   // 创业板ETF
    expect(isOnchainEtf('160644')).toBe(true)   // LOF —— 旧规则只认 159 会把它误判成场外
  })

  it('场外基金代码(0 开头 6 位)不是场内', () => {
    // 判错 → 按券商佣金算费用、不走 T+1 净值确认, 一整套口径都错
    expect(isOnchainEtf('012922')).toBe(false)
    expect(isOnchainEtf('008702')).toBe(false)
  })

  it('位数不对一律不算', () => {
    expect(isOnchainEtf('51030')).toBe(false)
    expect(isOnchainEtf('5103001')).toBe(false)
    expect(isOnchainEtf('')).toBe(false)
    expect(isOnchainEtf(undefined)).toBe(false)
  })

  it('A股正股不是场内 ETF', () => {
    expect(isOnchainEtf('600519')).toBe(false)
    expect(isOnchainEtf('300750')).toBe(false)
  })
})
