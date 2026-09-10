import { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'
import SeatHistoryModal from '../SeatHistoryModal'
import { MinuteChart } from './MinuteChart'
import { fmt } from './shared'

// 「点某根蜡烛 → 看那一天」的浮层。原来整块长在 ProKline 里: 13 个 state 有 8 个、
// 6 个 effect 有 3 个、160 行 JSX 有 81 行都属于它 —— 画图和"看某一天"本是两件事,
// 只是共用 intraday 一个变量当开关, 于是缠在一个 554 行的组件里。
//
// 分工: 开不开 / 开哪一天由父组件的 intraday 决定(点蜡烛时设的); 开了之后拉分时、
// 拉席位、算除权基准、ESC 关闭, 全在这里。
//
// getBars 传函数不传数组: bars 在父组件是 ref(K线按需增量加载, 不进 state)。
// 传函数就总能读到最新值, 不会因为 ref 身份不变而拿到旧数据。
export function DayOverlay({ day, code, getBars, onClose, onJumpDay }) {
  const [ovTab, setOvTab] = useState('分时')        // 浮层页签: 分时 | 龙虎榜
  const [minData, setMinData] = useState(null)
  const [minErr, setMinErr] = useState('')
  const [lhb, setLhb] = useState(null)             // 该日席位明细(懒加载)
  const [seatQ, setSeatQ] = useState('')           // 点席位名 → 该席位历史弹窗
  const ovBodyRef = useRef(null)
  const [minH, setMinH] = useState(200)            // 分时 viewBox 高: 按浮层实际宽高比算, 铺满不留白

  // 浮层: 拉该日分钟数据; ESC 关闭; 龙虎榜页签懒加载
  useEffect(() => {
    if (!day) return
    let alive = true
    setMinData(null); setMinErr(''); setLhb(null); setOvTab(day.tab || '分时')
    fetchJSON(`/api/market/tdx/minute/${encodeURIComponent(code)}?date=${day.date}`)
      .then(d => {
        if (!alive) return
        if (!d?.enabled) setMinErr('分时需启用 TDX 数据源(设置→TDX)')
        else if (!d?.data?.points?.length) setMinErr('该日分时不可得(太久远或非交易日)')
        else setMinData(d.data)
      })
      .catch(e => alive && setMinErr(e?.message || '加载失败'))
    const onEsc = (e) => { if (e.key === 'Escape') { onClose() } }
    window.addEventListener('keydown', onEsc)
    return () => { alive = false; window.removeEventListener('keydown', onEsc) }
  }, [day, code])

  // 分时区实际宽高比 → viewBox 高度(svg 按 720:minH 缩放正好占满容器, 大屏不再上浮留白)
  useEffect(() => {
    if (!day || ovTab !== '分时') return
    const el = ovBodyRef.current
    if (!el) return
    const compute = () => {
      const w = el.clientWidth || 720, h = el.clientHeight || 200
      // 下限 170: 容器越宽 720*h/w 越小, 宽屏下会压到 140 出头, 价格区所剩无几。
      // 抬到 170 后即使触底, MinuteChart 按比例分配也还能留出 ~60 单位画价格。
      // 代价是极宽屏下 svg 按高度贴合、左右留一点白, 好过刻度糊成一片。
      setMinH(Math.max(170, Math.round(720 * h / w)))
    }
    compute()
    const ro = new ResizeObserver(compute)
    ro.observe(el)
    return () => ro.disconnect()
  }, [day, ovTab, minData])

  // 基准昨收的复权错位校正: K线昨收是前复权价, TDX 历史分时是当日真实成交价——
  // 除权日前后两个标度错位, 会算出"主板+13%"的假涨跌。同一天(分时收盘 vs 该日前复权收盘)
  // 给出错位量: 现金分红除息是**减法**调整(qfq=raw-每股分红), 昨收按差值平移还原;
  // 送转/拆分才是乘法(错位比例大), 按比值折算。正常日/当日盘中错位≈0 不动。
  const { adjPrev, adjusted } = (() => {
    const pc = day?.prevClose
    const pts = minData?.points
    if (!pc || !pts?.length) return { adjPrev: pc, adjusted: false }
    // 首选后端精确值: 前复权昨收经分红送配事件表逐事件逆变换的真实昨收
    if (minData.prev_close_raw > 0) {
      return { adjPrev: minData.prev_close_raw,
               adjusted: (minData.exright_events || 0) > 0 ? '精确' : false }
    }
    // 回退近似: 用当日(分时收盘 vs 前复权收盘)错位量推——除息按差值平移, 送转按比值
    const bar = getBars().find(b => b.time === day.date)
    const lastPx = pts[pts.length - 1]?.price
    if (bar?.close > 0 && lastPx > 0) {
      const diff = lastPx - bar.close
      if (Math.abs(diff) > bar.close * 0.004) {
        return Math.abs(diff) < bar.close * 0.15
          ? { adjPrev: pc + diff, adjusted: '近似' }
          : { adjPrev: pc * (lastPx / bar.close), adjusted: '近似' }
      }
    }
    return { adjPrev: pc, adjusted: false }
  })()

  useEffect(() => {
    if (!day || ovTab !== '龙虎榜' || lhb) return
    let alive = true
    fetchJSON(`/api/market/lhb-detail/${encodeURIComponent(code)}?date=${day.date}`)
      .then(d => alive && setLhb(d || { note: '暂不可达' }))
      .catch(() => alive && setLhb({ note: '龙虎榜数据暂不可达(东财抖动)' }))
    return () => { alive = false }
  }, [ovTab, day, code, lhb])

  return (
    <>
      {/* 分时浮层: 覆盖组件底部(含量额副图区) —— 挂在根层而非主图容器内,
          否则 62% 只按被副图挤小后的主图高算, 纵轴刻度会挤成一团。 */}
      <div className="absolute inset-x-0 bottom-0 z-20 border-t border-border rounded-t-lg px-2 pt-1 pb-1.5 overflow-hidden flex flex-col"
        style={{ height: '62%',
                 background: 'color-mix(in srgb, var(--color-surface-2) 94%, transparent)', backdropFilter: 'blur(2px)' }}>
        <div className="flex items-baseline gap-2 px-1 mb-0.5">
          <span className="text-[11px] font-mono text-text-bright">{(minData?.date || day.date).toString().replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')}</span>
          {['分时', '龙虎榜'].map(t => (
            <button key={t} onClick={() => setOvTab(t)}
              className={`text-[10.5px] px-1.5 py-0.5 rounded cursor-pointer ${ovTab === t ? 'bg-accent/20 text-accent' : 'text-text-dim hover:text-text'}`}>
              {t}
            </button>
          ))}
          <span className="text-[9.5px] text-text-dim">{ovTab === '分时' ? `基准=${day?.prevIsOpen ? '开盘' : '前收'} ${fmt(adjPrev)}${adjusted ? `(除权校正·${adjusted})` : ''} · ` : ''}点K线空白处收起</span>
          <button onClick={onClose}
            className="ml-auto text-text-dim hover:text-text text-[15px] leading-none px-1 cursor-pointer">×</button>
        </div>
        {ovTab === '分时' && (
          <div ref={ovBodyRef} className="flex-1 min-h-0">
            {minErr && <div className="text-center py-6 text-[11.5px] text-text-dim">{minErr}</div>}
            {!minErr && !minData && <div className="text-center py-6 text-[11.5px] text-text-dim">分时加载中…</div>}
            {minData && (
              <MinuteChart points={minData.points} prevClose={adjPrev}
                day={minData.date || day.date} height={minH} />
            )}
          </div>
        )}
        {ovTab === '龙虎榜' && (
          !lhb ? <div className="flex-1 flex items-center justify-center text-[11.5px] text-text-dim">席位明细加载中…</div>
          : (!lhb['买入']?.length && !lhb['卖出']?.length)
          ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2 text-[11.5px] text-text-dim px-4 text-center">
              <span>{lhb.note || '该日未上龙虎榜'}</span>
              {(lhb['最近上榜日'] || []).length > 0 && (
                <span className="flex items-baseline gap-1.5 flex-wrap justify-center">
                  <span className="text-[10.5px]">它最近的上榜日:</span>
                  {lhb['最近上榜日'].map(d => {
                    const arr = getBars()
                    const i = arr.findIndex(b => b.time === d)
                    return i >= 0
                      ? <button key={d}
                          onClick={() => { onJumpDay({ date: d, prevClose: arr[i - 1]?.close ?? arr[i].open, prevIsOpen: !arr[i - 1], tab: '龙虎榜' }) }}
                          className="text-[10.5px] px-1.5 py-0.5 rounded bg-accent/15 text-accent hover:bg-accent/25 cursor-pointer font-mono">
                          {d.slice(2)}
                        </button>
                      : <span key={d} className="text-[10.5px] font-mono text-text-muted" title="超出当前K线窗口">{d.slice(2)}</span>
                  })}
                </span>
              )}
            </div>
          )
          : (
            <div className="flex-1 min-h-0 flex flex-col text-[12.5px] overflow-y-auto">
              {lhb['上榜原因'] && <div className="px-1 text-[10.5px] text-text-dim mb-1 shrink-0">上榜原因: {lhb['上榜原因']}</div>}
              <div className="grid grid-cols-2 gap-5 px-1 flex-1 content-start">
                {[['买入', 'text-bear-bright'], ['卖出', 'text-bull-bright']].map(([side, cls]) => (
                  <div key={side}>
                    <div className={`mb-1 text-[13px] font-semibold ${cls}`}>{side}前五 · 计 {(lhb[`${side}总计万`] / 1e4).toFixed(2)}亿</div>
                    {(lhb[side] || []).map((s, i) => (
                      <div key={i} className="flex items-baseline gap-1.5 py-1.5 border-b border-border-subtle/40">
                        {s.席位 === '机构专用' || s.席位.includes('股通')
                          ? <span className="text-text truncate flex-1" title={s.席位}>{s.席位.replace(/(股份|有限责任)?公司|证券营业部/g, '')}</span>
                          : <button onClick={() => setSeatQ(s.席位)} title={`${s.席位} · 点击看该席位近90天上榜记录`}
                              className="text-text truncate flex-1 text-left cursor-pointer hover:text-accent underline decoration-dotted decoration-border underline-offset-2">
                              {s.席位.replace(/(股份|有限责任)?公司|证券营业部/g, '')}
                            </button>}
                        {s.标签 && <span className="text-[10px] px-1 rounded bg-accent/15 text-accent shrink-0">{s.标签}</span>}
                        <span className="text-[10.5px] text-text-dim font-mono shrink-0">{s['占成交%']}%</span>
                        <span className={`font-mono font-semibold shrink-0 ${cls}`}>{(s.金额万 / 1e4).toFixed(2)}亿</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div className="px-1 pt-1 text-[9.5px] text-text-dim shrink-0">{lhb.note}</div>
            </div>
          )
        )}
      </div>
    {seatQ && <SeatHistoryModal seat={seatQ} onClose={() => setSeatQ('')} />}
    </>
  )
}
