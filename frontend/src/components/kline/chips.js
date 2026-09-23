// 筹码分布(Grinblatt & Han 2005 的换手率加权成本): 第 i 天的成交, 被之后每天的换手按比例换走,
// 剩下的量 = 那天留在手里的筹码。t 日收盘时第 i 天的筹码权重
//   w_i = V_i · Π_{j=i+1..t} (1 − V_j),   V = 换手率 = 成交量(手)×100 / 当日流通股本
// 每天的筹码按均匀分布摊到当天 [最低, 最高](前复权价)。只统计已加载的 K 线, 更早的筹码算不到
// (cover = 窗口内筹码占比, 低换手的票可能偏低)。
// 全市场检验(私有研究): 浮盈/获利比例扣掉前期涨跌后对之后涨跌没有预测力 —— 这里只做事实展示。

const PROFIT = 'rgba(207,92,92,0.42)', LOSS = 'rgba(95,168,108,0.42)', AVG = '#e8c77a'

// 流通股本变动表 [[YYYY-MM-DD, 股], ...] → 每根 bar 的换手率(取不到为 null)
export function turnoverFor(bars, schedule) {
  if (!schedule?.length) return null
  let k = 0
  return bars.map(b => {
    while (k + 1 < schedule.length && schedule[k + 1][0] <= b.time) k++
    const fl = schedule[k][0] <= b.time ? schedule[k][1] : schedule[0][1]
    if (!fl || !b.volume) return null
    return Math.min(1, (b.volume * 100) / fl)
  })
}

export function chipDist(bars, turn, t, nb = 90) {
  if (!turn || t == null || t < 0 || t >= bars.length) return null
  let surv = 1, lo = Infinity, hi = -Infinity
  const items = []
  for (let i = t; i >= 0 && surv > 1e-3; i--) {
    const v = turn[i]
    if (v == null) continue
    const b = bars[i]
    const w = v * surv
    surv *= 1 - v
    items.push([b.low, b.high, w])
    if (b.low < lo) lo = b.low
    if (b.high > hi) hi = b.high
  }
  if (!items.length || !(hi > lo)) return null
  const step = (hi - lo) / nb
  const bins = new Float64Array(nb)
  let total = 0, pv = 0
  for (const [l, h, w] of items) {
    total += w
    pv += w * (l + h) / 2
    if (h - l < step * 1e-6) { bins[Math.min(nb - 1, Math.floor((l - lo) / step))] += w; continue }
    const a = (l - lo) / step, z = (h - lo) / step
    for (let k = Math.floor(a); k < Math.min(nb, Math.ceil(z)); k++) {
      const ov = Math.min(z, k + 1) - Math.max(a, k)
      if (ov > 0) bins[k] += w * ov / (z - a)
    }
  }
  const price = bars[t].close
  let winner = 0, cum = 0, p5 = null, p95 = null
  for (let k = 0; k < nb; k++) {
    const w = bins[k] / total
    const b0 = lo + k * step
    winner += w * Math.max(0, Math.min(1, (price - b0) / step))
    const c2 = cum + w
    if (p5 == null && c2 >= 0.05) p5 = b0 + step * ((0.05 - cum) / (w || 1))
    if (p95 == null && c2 >= 0.95) p95 = b0 + step * ((0.95 - cum) / (w || 1))
    cum = c2
  }
  return {
    time: bars[t].time, lo, step, bins: Array.from(bins, x => x / total), price,
    avg: pv / total, winner, p5, p95, cover: 1 - surv,
  }
}

class ChipRenderer {
  constructor(d) { this._d = d }
  draw(target) {
    const d = this._d
    if (!d?.rows?.length) return
    target.useBitmapCoordinateSpace(scope => {
      const ctx = scope.context, hr = scope.horizontalPixelRatio, vr = scope.verticalPixelRatio
      const W = scope.mediaSize.width
      const maxLen = W * 0.2, right = W - 2
      const mx = Math.max(...d.rows.map(r => r.w)) || 1
      for (const r of d.rows) {
        if (r.y1 == null || r.y2 == null) continue
        const len = (r.w / mx) * maxLen
        const top = Math.min(r.y1, r.y2), h = Math.max(1, Math.abs(r.y2 - r.y1) - 0.5)
        ctx.fillStyle = r.profit ? PROFIT : LOSS
        ctx.fillRect((right - len) * hr, top * vr, len * hr, h * vr)
      }
      if (d.yAvg != null) {
        ctx.strokeStyle = AVG; ctx.lineWidth = Math.max(1, vr)
        ctx.setLineDash([4 * hr, 3 * hr])
        ctx.beginPath(); ctx.moveTo((right - maxLen) * hr, d.yAvg * vr); ctx.lineTo(right * hr, d.yAvg * vr); ctx.stroke()
        ctx.setLineDash([])
      }
    })
  }
}
class ChipView {
  constructor(src) { this._src = src; this._d = null }
  update() {
    const { series, dist } = this._src
    if (!series || !dist) { this._d = null; return }
    const rows = dist.bins.map((w, k) => {
      const p0 = dist.lo + k * dist.step
      return { w, profit: p0 + dist.step / 2 <= dist.price,
               y1: series.priceToCoordinate(p0 + dist.step), y2: series.priceToCoordinate(p0) }
    }).filter(r => r.w > 0)
    this._d = { rows, yAvg: series.priceToCoordinate(dist.avg) }
  }
  renderer() { return new ChipRenderer(this._d) }
  zOrder() { return 'bottom' }     // 垫在蜡烛下面, 最近几根蜡烛照样看得清
}
export class ChipPrimitive {
  constructor() { this.dist = null; this.series = null; this._view = new ChipView(this) }
  attached(p) { this.series = p.series; this._req = p.requestUpdate }
  detached() { this.series = null }
  updateAllViews() { this._view.update() }
  paneViews() { return [this._view] }
  setDist(d) { this.dist = d; this._req?.() }
}
