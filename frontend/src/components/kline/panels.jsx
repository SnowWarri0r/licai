import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import { DOWN, UP, colorPct, fmtHand } from './shared'

// ---------------------------------------------------------------------------
// 五档盘口
// ---------------------------------------------------------------------------
export function OrderBook({ data, prevClose, decimals = 2 }) {
  if (!data) return null
  // 五档按标的精度显示(A股 2 位 / ETF 3 位), 否则 178.28/178.29 会被压成同一个 178.3, 看着像没聚类
  const px = (p) => p == null ? '--' : <span className={colorPct(prevClose ? ((p / prevClose) - 1) * 100 : 0)}>{p.toFixed(decimals)}</span>
  const maxVol = Math.max(1, ...[...(data.bids || []), ...(data.asks || [])].map(l => Number(l['手']) || 0))
  const Row = ({ lvl, side, idx }) => (
    <div className="relative flex justify-between items-center px-1.5 py-[3px] text-[11px] font-mono">
      <div className="absolute inset-y-0 right-0 rounded-sm" style={{ width: `${(Number(lvl['手']) || 0) / maxVol * 100}%`, background: side === 'ask' ? 'rgba(95,168,108,.13)' : 'rgba(207,92,92,.13)' }} />
      <span className="relative text-text-muted">{side === 'ask' ? '卖' : '买'}{idx}</span>
      <span className="relative">{px(lvl.price)}</span>
      <span className="relative text-text-dim">{Math.round(Number(lvl['手']) || 0)}</span>
    </div>
  )
  return (
    <div>
      <div className="text-[10.5px] text-text-muted mb-1 flex justify-between"><span>五档盘口</span><span>手</span></div>
      {[...(data.asks || [])].slice(0, 5).reverse().map((l, i, arr) => <Row key={'a' + i} lvl={l} side="ask" idx={arr.length - i} />)}
      <div className="border-t border-border-subtle my-0.5" />
      {(data.bids || []).slice(0, 5).map((l, i) => <Row key={'b' + i} lvl={l} side="bid" idx={i + 1} />)}
      <div className="flex justify-between text-[10.5px] mt-1.5 px-1.5">
        <span className="text-bull">内盘 {fmtHand(data['内盘手'])}</span>
        <span className="text-bear">外盘 {fmtHand(data['外盘手'])}</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 逐笔成交
// ---------------------------------------------------------------------------
export function Ticks({ ticks, decimals = 2 }) {
  if (!ticks?.length) return null
  const vols = ticks.map(t => Number(t['手']) || 0)
  const avg = vols.reduce((a, b) => a + b, 0) / (vols.length || 1)
  const bigThresh = Math.max(avg * 3, 100)   // 大单: ≥均量3倍且≥100手
  return (
    <div>
      <div className="text-[10.5px] text-text-muted mb-1 flex justify-between"><span>逐笔成交</span><span className="text-text-muted/70">大单加亮</span></div>
      <div className="max-h-[150px] overflow-y-auto pr-1">
        {ticks.map((t, i) => {
          const v = Math.round(Number(t['手']) || 0)
          const big = v >= bigThresh
          const dc = t.dir === '买' ? UP : t.dir === '卖' ? DOWN : 'var(--color-text-muted)'
          return (
            <div key={i} className="flex justify-between items-center text-[10.5px] font-mono py-[2px]"
              style={big ? { background: t.dir === '买' ? 'rgba(207,92,92,.12)' : t.dir === '卖' ? 'rgba(95,168,108,.12)' : 'transparent', borderRadius: 3 } : undefined}>
              <span className="text-text-muted px-1">{t.time}</span>
              <span className={big ? 'text-text-bright' : 'text-text'}>{t.price.toFixed(decimals)}</span>
              <span className="px-1" style={{ color: dc, fontWeight: big ? 700 : 400 }}>{v}{t.dir === '买' ? '↑' : t.dir === '卖' ? '↓' : ''}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 主弹窗
// ---------------------------------------------------------------------------
// 深度龙虎榜(可选扩展数据源): 折叠, 点开才拉(要凭证的接口, 别每次开K线都打)。席位带游资身份标签。
export function LhbPanel({ code }) {
  const [open, setOpen] = useState(false)
  const [d, setD] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')
  // ⚠️ 必须跟着 code 走: 榜单里这个面板是**常驻**的(持仓弹窗里才随弹窗销毁重建),
  // 原来只在展开时 load 且 `if (d) return`, 换股票后会一直显示上一只票的龙虎榜。
  useEffect(() => {
    if (!open || !code) return
    let alive = true
    setLoading(true); setErr(''); setD(null)
    fetchJSON(`/api/market/provider/stock-lhb/${encodeURIComponent(code)}`)
      .then(r => {
        if (!alive) return
        if (r?.available === false) { setErr('unavailable'); return }
        if (r?.need_login) { setErr('need_login'); return }
        if (r?.error) { setErr(r.error); return }
        setD(r)
      })
      .catch(e => { if (alive) setErr(String(e?.message || e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [open, code])

  const toggle = () => setOpen(v => !v)
  const yi = v => v == null ? '' : `${v}亿`

  return (
    <div className="mt-3 border-t border-border-subtle pt-2">
      <button onClick={toggle} className="flex items-center gap-1.5 text-[12px] text-text-dim hover:text-text cursor-pointer">
        <span className={`transition-transform ${open ? 'rotate-90' : ''}`}>▸</span>
        深度龙虎榜 <span className="text-[10px] text-text-muted">扩展数据源 · 席位带游资标签</span>
      </button>
      {open && (
        <div className="mt-2">
          {loading && <div className="text-[11px] text-text-dim">拉取中…</div>}
          {err === 'unavailable' && (
            <div className="text-[11px] text-text-muted">
              这一层席位身份标签由可选的扩展数据源提供，当前未接入 — 上面的龙虎榜(东财口径)不受影响。
            </div>
          )}
          {err === 'need_login' && (
            <div className="text-[11px] text-warn">
              扩展数据源凭证已失效 — 去 设置 → 扩展数据源 重新填一次。
            </div>
          )}
          {err && err !== 'need_login' && err !== 'unavailable' && <div className="text-[11px] text-text-muted">{err}</div>}
          {d && !d.上榜 && <div className="text-[11px] text-text-muted">{d.note}</div>}
          {d && d.上榜 && (
            <div className="space-y-2">
              <div className="text-[11px] text-text-dim flex flex-wrap gap-x-3">
                <span>{d.date}</span>
                <span>龙虎榜成交 <span className="font-mono text-text">{yi(d.龙虎榜成交额亿)}</span></span>
                <span>买入合计 <span className="font-mono text-bear-bright">{yi(d.买入合计亿)}</span></span>
                {d.连板数 > 0 && <span className="text-accent">{d.连板数}连板</span>}
              </div>
              {(d.席位 || []).map((b, i) => (
                <div key={i} className="bg-surface-3 rounded p-2">
                  {b.上榜原因 && <div className="text-[10.5px] text-text-muted mb-1">{Array.isArray(b.上榜原因) ? b.上榜原因.join(' · ') : b.上榜原因}</div>}
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                    <div>
                      <div className="text-[10px] text-bear-bright mb-0.5">买入 {yi(b.买入合计亿)}</div>
                      {(b.买入席位 || []).slice(0, 5).map((s, j) => (
                        <div key={j} className="text-[10.5px] text-text-dim flex justify-between gap-2">
                          <span className="truncate">{s.营业部}{s.标签 && <span className="text-accent ml-1">{s.标签.join('/')}</span>}</span>
                          <span className="font-mono text-bear-bright shrink-0">{yi(s.买入亿)}</span>
                        </div>
                      ))}
                    </div>
                    <div>
                      <div className="text-[10px] text-bull-bright mb-0.5">卖出 {yi(b.卖出合计亿)}</div>
                      {(b.卖出席位 || []).slice(0, 5).map((s, j) => (
                        <div key={j} className="text-[10.5px] text-text-dim flex justify-between gap-2">
                          <span className="truncate">{s.营业部}{s.标签 && <span className="text-accent ml-1">{s.标签.join('/')}</span>}</span>
                          <span className="font-mono text-bull-bright shrink-0">{yi(s.卖出亿)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
              <div className="text-[10px] text-text-muted">深度龙虎榜(扩展数据源) · 标签为游资/机构身份识别 · 已披露数据不构成买卖建议</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
