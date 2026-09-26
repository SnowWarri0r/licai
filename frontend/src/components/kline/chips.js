// 筹码分布(Grinblatt & Han 2005 的换手率加权成本): 第 i 天的成交, 被之后每天的换手按比例换走,
// 剩下的量 = 那天留在手里的筹码。t 日收盘时第 i 天的筹码权重
//   w_i = V_i · Π_{j=i+1..t} (1 − V_j),   V = 换手率 = 成交量(手)×100 / 当日流通股本
// 每天的筹码按均匀分布摊到当天 [最低, 最高](前复权价)。只统计已加载的 K 线, 更早的筹码算不到
// (cover = 窗口内筹码占比, 低换手的票可能偏低)。
// 全市场检验(私有研究): 浮盈/获利比例扣掉前期涨跌后对之后涨跌没有预测力 —— 这里只做事实展示。

const PROFIT = 'rgba(207,92,92,0.55)', LOSS = 'rgba(95,168,108,0.55)'
export const CHIP_AVG = '#b39ddb'     // 平均成本: 淡紫, 与成本线(金)/昨收(灰蓝)区分
export const CHIP_W = 110             // 独立筹码栏宽度(px)

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

// 画进主图右侧的独立画布(不叠在 K 线上)。纵坐标用主图 series.priceToCoordinate, 与主图价格刻度逐像素对齐。
export function drawChipCanvas(canvas, height, d) {
  if (!canvas) return
  const dpr = window.devicePixelRatio || 1
  const W = canvas.clientWidth || CHIP_W
  if (height > 0 && canvas.style.height !== `${height}px`) canvas.style.height = `${height}px`
  const H = height || canvas.clientHeight
  if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr)
  }
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)
  if (!d?.rows?.length) return
  const left = 4, maxLen = W - 10
  const mx = Math.max(...d.rows.map(r => r.w)) || 1
  for (const r of d.rows) {
    if (r.y1 == null || r.y2 == null) continue
    const top = Math.min(r.y1, r.y2), h = Math.max(1, Math.abs(r.y2 - r.y1) - 0.6)
    ctx.fillStyle = r.profit ? PROFIT : LOSS
    ctx.fillRect(left, top, (r.w / mx) * maxLen, h)
  }
  if (d.yPrice != null) {                        // 当日收盘价: 细实线
    ctx.strokeStyle = 'rgba(230,230,235,0.55)'; ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(0, d.yPrice + 0.5); ctx.lineTo(W, d.yPrice + 0.5); ctx.stroke()
  }
  if (d.yAvg != null) {                          // 平均成本: 淡紫虚线 + 标注
    ctx.strokeStyle = CHIP_AVG; ctx.lineWidth = 1.2; ctx.setLineDash([4, 3])
    ctx.beginPath(); ctx.moveTo(0, d.yAvg + 0.5); ctx.lineTo(W, d.yAvg + 0.5); ctx.stroke()
    ctx.setLineDash([])
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace'
    ctx.fillStyle = CHIP_AVG; ctx.textAlign = 'right'
    const ty = d.yAvg < 14 ? d.yAvg + 12 : d.yAvg - 3
    ctx.fillText(`均 ${d.avg >= 100 ? d.avg.toFixed(1) : d.avg.toFixed(2)}`, W - 3, ty)
  }
}

class NoopRenderer { draw() {} }
class ChipView {
  constructor(src) { this._src = src }
  // 主图每次重绘(缩放/拖动/改量程)都会调 update: 这里重算坐标, 交给外部画布
  update() {
    const { series, dist, onFrame } = this._src
    if (!onFrame) return
    if (!series || !dist) { onFrame(null); return }
    const rows = dist.bins.map((w, k) => {
      const p0 = dist.lo + k * dist.step
      return { w, profit: p0 + dist.step / 2 <= dist.price,
               y1: series.priceToCoordinate(p0 + dist.step), y2: series.priceToCoordinate(p0) }
    }).filter(r => r.w > 0)
    onFrame({ rows, avg: dist.avg, yAvg: series.priceToCoordinate(dist.avg), yPrice: series.priceToCoordinate(dist.price) })
  }
  renderer() { return new NoopRenderer() }
}
export class ChipPrimitive {
  constructor() { this.dist = null; this.series = null; this.onFrame = null; this._view = new ChipView(this) }
  attached(p) { this.series = p.series; this._req = p.requestUpdate }
  detached() { this.series = null }
  updateAllViews() { this._view.update() }
  paneViews() { return [this._view] }
  setDist(d) { this.dist = d; this._req?.(); if (!d) this.onFrame?.(null) }
}
