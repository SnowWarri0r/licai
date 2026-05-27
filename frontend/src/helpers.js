export function fmtPrice(v) {
  if (v == null || v === 0) return '--'
  return parseFloat(v).toFixed(2)
}

export function fmtCost(v) {
  if (v == null) return '--'
  return parseFloat(v).toFixed(4)
}

export function fmtPct(v) {
  if (v == null) return '--'
  const sign = v > 0 ? '+' : ''
  return `${sign}${parseFloat(v).toFixed(2)}%`
}

export function fmtMoney(v) {
  if (v == null) return '--'
  const abs = Math.abs(v)
  if (abs >= 10000) return (v / 10000).toFixed(2) + '万'
  return v.toFixed(2)
}

export function priceColor(v) {
  if (v == null || v === 0) return 'text-text-dim'
  return v > 0 ? 'text-bear-bright' : 'text-bull-bright'
}

export function signalLabel(s) {
  return { STRONG: '强信号', MODERATE: '中等', WEAK: '弱信号' }[s] || s
}

export function signalColor(s) {
  return {
    STRONG: 'bg-signal-strong text-white',
    MODERATE: 'bg-signal-moderate text-black',
    WEAK: 'bg-signal-weak text-text-dim',
    CLOSED: 'bg-signal-closed text-text-dim',
  }[s] || 'bg-signal-closed text-text-dim'
}

export function fmtDays(n) {
  if (n == null) return '--'
  if (n < 30) return `${n}天`
  if (n < 365) return `${Math.round(n / 30)}月`
  return `${(n / 365).toFixed(1)}年`
}

export function fmtHealthColor(level) {
  if (level === 'green') return 'text-bull-bright'
  if (level === 'yellow') return 'text-signal-moderate'
  if (level === 'red') return 'text-bear-bright'
  return 'text-text-dim'
}

export function fmtHealthEmoji(level) {
  return { green: '🟢', yellow: '🟡', red: '🔴' }[level] || '⚪'
}

export function fmtHealthLabel(level) {
  return { green: '可加仓', yellow: '仅浅档', red: '暂停加仓' }[level] || '未知'
}

// ============================================================
// 基金底层穿透 — 把 FUND 按名字推断成 6 个大类之一 (A/F/W/M/C, 不分 H/U)
// 用于 AllocationAdvisor 大类分布 + UnifiedPortfolio 集中度警告.
//
// 返回值含义:
//   A : A 股 ETF / 行业 ETF / A 股宽基 (沪深300/中证500/创业板/科创50/科创100)
//       —— 因为 UnifiedPortfolio 把 A/H/U 都堆在 A 桶, 这里跟随同样口径
//   F : 混合型 / 股票型 / 无法识别底层的兜底 (基金作为载体仍展示一部分)
//   W : 债券型 (跟银证理财同档"低风险固收")
//   M : 货币 / 活期 / 余额宝 类 (T+0 流动性)
//   C : 黄金 / 白银 / 商品 ETF (跟加密一起算"另类")
//
// 注意: 海外 ETF (QDII/纳指/标普/恒生/日经/中概) 也归 A —— 因为 UI 没区分 H/U.
// AllocationAdvisor 自己有更细的 A/H/U 拆分, 由它另写穿透.
// ============================================================
export function fundPassthroughType(name = '') {
  const n = String(name)
  // 现金类
  if (/货币|余额宝|活期|零钱通|现金管理/.test(n)) return 'M'
  // 债券类 (含"稳健增利"等口径)
  if (/债券|国债|短债|城投|信用债|稳健增利|纯债|利率债/.test(n)) return 'W'
  // 商品 (贵金属 + 原油)
  if (/黄金|白银|金\s*ETF|银\s*ETF|原油|石油\s*ETF|商品/.test(n)) return 'C'
  // 其他识别为权益 (A 股 / 海外 / 港股)
  if (/QDII|纳斯达克|纳指|标普|美股|港股|恒生|中概|海外|日经|越南|印度|欧洲/.test(n)) return 'A'
  if (/沪深|中证|上证|创业|科创|A\s*股|国证|AIDC|数据中心/.test(n)) return 'A'
  if (/股票|混合|主题|行业|精选|成长|价值|蓝筹|医药|消费|科技|新能源|半导体|芯片|信息|算力|电力|金融|银行|证券|地产|材料|机械|军工|汽车|有色|钢铁|煤炭/.test(n)) return 'A'
  // 兜底: 看不出就保留为基金桶 (例如纯货基/纯债基命名变体未匹配)
  return 'F'
}

// AllocationAdvisor 专用: 10 桶按实质穿透.
// 返回值:
//   M=现金/货基 / W=银证理财 / BND=债基 / A=A股(直接+ETF穿透)
//   H=港股 / U=美股 / OS=其它海外(日/欧/越/印) / CMD=商品(贵金属/原油)
//   CRY=加密(直接+BOT) / F=基金兜底(识别不出底层)
export function fundPassthroughBucketDetailed(name = '') {
  const n = String(name)
  if (/货币|余额宝|活期|零钱通|现金管理/.test(n)) return 'M'
  if (/债券|国债|短债|城投|信用债|稳健增利|纯债|利率债/.test(n)) return 'BND'
  if (/黄金|白银|金\s*ETF|银\s*ETF|原油|石油\s*ETF|商品/.test(n)) return 'CMD'
  if (/港股|恒生|H\s*股|中概/.test(n)) return 'H'
  if (/QDII|纳斯达克|纳指|标普|美股|全球(?!财)/.test(n)) return 'U'
  if (/日经|TOPIX|东证|越南|印度|欧洲|德国DAX|法国|英国|海外(?!债)/.test(n)) return 'OS'
  // 默认 A (国内股票型基金 / A 股宽基 / 行业 / 主题)
  if (/沪深|中证|上证|创业|科创|A\s*股|国证|AIDC|数据中心|股票|混合|主题|行业|精选|成长|价值|蓝筹|医药|消费|科技|新能源|半导体|芯片|信息|算力|电力|金融|银行|证券|地产|材料|机械|军工|汽车|有色|钢铁|煤炭/.test(n)) return 'A'
  return 'F'
}
