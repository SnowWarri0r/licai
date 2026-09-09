import { fmtMoney, isOnchainEtf } from '../../helpers'
import { A_SECTORS, FUND_CATEGORIES, MIN_HOLDER_PCT } from './constants'

export function stockMarketOfCode(code = '') {
  const c = String(code).toUpperCase()
  if (c.startsWith('HK.')) return 'HK'
  if (c.startsWith('US.')) return 'US'
  return 'A'
}

export function stockCodeForMarket(market, code) {
  const raw = String(code || '').trim().toUpperCase()
  if (market === 'HK') return `HK.${raw.replace(/^HK\.?/, '').padStart(5, '0')}`
  if (market === 'US') return `US.${raw.replace(/^US\.?/, '')}`
  return raw.replace(/^A\./, '')
}

export function currencySymbol(currency = 'CNY') {
  if (currency === 'USD') return '$'
  if (currency === 'HKD') return 'HK$'
  return '¥'
}

export function formatCurrencyMoney(currency, value) {
  return `${currencySymbol(currency)}${fmtMoney(value)}`
}

export function fxSourceLabel(source) {
  if (source === 'sina_bid_ask_mid') return '新浪外汇买卖价中间价'
  if (source === 'fallback') return '备用汇率'
  if (source === 'CNY') return '人民币'
  return source || '汇率'
}

export function fundCategoryOf(name = '') {
  for (const c of FUND_CATEGORIES) if (c.match.test(name)) return c
  return { id: 'other', label: '其他' }
}

function aShareCategoryOf(name = '', sector = '') {
  // 优先直接用后端东财真实一级行业(如 "有色金属"/"电子设备"/"电气设备")当分类 ——
  // 这是权威数据, 比粗分桶准。后端拿不到(网络失败)才回退按股票名 regex 兜底。
  const s = String(sector || '').split('-')[0].trim()
  if (s) return { id: s, label: s }
  for (const c of A_SECTORS) if (c.match.test(name)) return c
  return { id: 'other', label: '其他' }
}

export function stockCategoryOf(h) {
  const market = h.market || stockMarketOfCode(h.stock_code)
  if (market === 'HK') return { id: 'hk', label: '港股' }
  if (market === 'US') return { id: 'us', label: '美股' }
  return aShareCategoryOf(h.stock_name, h.sector)
}

                               // 混进一只光模块(实测 392 元)也算"同源"的话, 角标就成噪音了
export function buildFamilyIndex(expo) {
  if (!expo || !Array.isArray(expo.industries)) return null
  const total = expo.total || 0
  const floor = total * MIN_HOLDER_PCT / 100
  const byRow = new Map()      // "A:600547" -> [行业名]
  const fams = new Map()       // 行业名 -> {holders, mv}
  for (const g of expo.industries) {
    const ind = g.industry || ''
    if (!ind || ind === '未知行业' || ind.startsWith('海外')) continue
    const holders = (g.holders || []).filter(h => (h.mv || 0) >= floor)
    if (holders.length < 2) continue
    // 家族的钱按**穿透后**算: 那几只基金并不是整只都在这个行业里
    fams.set(ind, { holders, mv: holders.reduce((s, h) => s + (h.mv || 0), 0) })
    for (const h of holders) {
      for (const c of (h.codes && h.codes.length ? h.codes : [h.code])) {
        const k = `${h.kind}:${c}`
        byRow.set(k, [...(byRow.get(k) || []), ind])
      }
    }
  }
  return { byRow, fams }
}

// 后端还没返回时的兜底(只认名字, 认不出的多了去了 —— 见上)。
// Returns array of family ids (a row can belong to multiple, e.g. 白银 is both silver + metals).
export function riskFamiliesOf(row) {
  if (row.type === 'A') {
    const name = row.name || ''
    const fams = []
    if (/白银/.test(name)) fams.push('silver', 'metals')
    else if (/黄金/.test(name)) fams.push('gold')
    else if (/有色|铜|铝|锌|镍|铅|锂|钴|稀土|钼/.test(name)) fams.push('metals')
    return fams
  }
  if (row.type === 'F') {
    const cid = row.category?.id
    if (cid === 'silver') return ['silver', 'metals']
    if (cid === 'gold') return ['gold']
    if (cid === 'commodity') return ['metals']
    if (cid === 'overseas') return ['overseas']
    if (cid === 'aindex') return ['cn_broad']
    return []
  }
  return []
}

// 场内 ETF/LOF 判定 — 统一走 isOnchainEtf (深 1xxxxx / 沪 5xxxxx)。
// 旧规则只认 5xxxxx/159xxx 会把 16xxxx 的 LOF (如互联网QD 160644) 误判成场外。
export const isEtfCode = isOnchainEtf
