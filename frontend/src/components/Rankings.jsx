import { useState, useEffect, useRef } from 'react'
import { fetchJSON } from '../hooks/useApi'
import ProKline from './ProKline'
import StockAskModal from './StockAskModal'

const TABS = [
  { key: 'gainers', label: '涨幅' },
  { key: 'by_amount', label: '成交额' },
  { key: 'coiled', label: '蓄势' },
  { key: 'unbroken', label: '强势' },
  { key: 'inst', label: '机构' },
  { key: 'earnings', label: '业绩' },
]

function pctColor(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 按代码前缀分板块
function boardOf(code) {
  const c = String(code || '')
  if (c.startsWith('688') || c.startsWith('689')) return '科创板'
  if (c.startsWith('30')) return '创业板'
  if (c[0] === '8' || c[0] === '4') return '北交所'
  return '主板'
}
const BOARDS = ['全部', '主板', '创业板', '科创板', '北交所']

// 右侧面板: 选中股票看 K线(铺满); 想问就点"问 AI"或底部输入框 → 弹出式对话(与问问市场样式一致)
function StockPanel({ stock }) {
  const [askOpen, setAskOpen] = useState(false)
  const [seed, setSeed] = useState('')
  const [draft, setDraft] = useState('')

  // 切换股票: 关弹窗、清空草稿
  useEffect(() => { setAskOpen(false); setSeed(''); setDraft('') }, [stock])

  const openAsk = (question = '') => { setSeed(question); setAskOpen(true) }
  const submitDraft = () => { const t = draft.trim(); if (t) { openAsk(t); setDraft('') } }

  if (!stock) {
    return (
      <div className="h-full flex items-center justify-center text-center px-6">
        <div className="text-text-muted text-[13px] leading-relaxed">
          点左侧任意一只股票看 K 线<br />
          <span className="text-[11px] text-text-dim">想问什么(为什么涨/量价/消息)点「问 AI」</span>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-baseline gap-2 px-4 py-2 border-b border-border-subtle shrink-0">
        <span className="text-[14px] font-semibold text-text-bright">{stock.name}</span>
        <span className="text-[11px] font-mono text-text-muted">{stock.code}</span>
        <span className={`text-[13px] font-mono font-semibold ${pctColor(stock.pct)}`}>
          {stock.pct >= 0 ? '+' : ''}{stock.pct}%
        </span>
        {stock['行业'] && <span className="text-[10.5px] text-text-dim ml-1">{stock['行业']}</span>}
        <button onClick={() => openAsk('')}
          className="ml-auto text-[11px] px-2.5 py-1 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30">
          问 AI 分析
        </button>
      </div>

      {/* K线铺满面板 */}
      <div className="flex-1 min-h-0 px-3 py-2">
        <ProKline code={stock.code} fill />
      </div>

      {/* 底部快捷提问: 回车/点问 → 弹出对话 */}
      <div className="shrink-0 border-t border-border px-3 py-2 flex gap-2">
        <input value={draft} onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) submitDraft() }}
          placeholder={`想问点 ${stock.name} 什么?例: 今天为什么这么走 / 量价怎么看`}
          className="flex-1 text-[12px] px-3 py-2 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none" />
        <button onClick={submitDraft} disabled={!draft.trim()}
          className="text-[12px] px-3.5 py-2 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40 disabled:cursor-not-allowed">
          问
        </button>
      </div>

      {askOpen && <StockAskModal stock={stock} initialQuestion={seed} onClose={() => setAskOpen(false)} />}
    </div>
  )
}

export default function Rankings() {
  const [tab, setTab] = useState(() => {
    // deep-link: #rankings?t=inst 直达指定页签
    const q = new URLSearchParams((window.location.hash.split('?')[1] || ''))
    const t = q.get('t')
    return TABS.some(x => x.key === t) ? t : 'gainers'
  })
  const [board, setBoard] = useState('全部')
  const [data, setData] = useState(null)
  const [coiled, setCoiled] = useState(null)
  const [unbroken, setUnbroken] = useState(null)
  const [inst, setInst] = useState(null)
  const [instSide, setInstSide] = useState('net_buy')   // net_buy | net_sell
  const [earnings, setEarnings] = useState(null)
  const [earnSide, setEarnSide] = useState('预喜')       // 预喜 | 预警 | 持仓关联
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)
  const [selected, setSelected] = useState(null)
  const listRef = useRef([])

  const load = () => {
    setLoading(true); setErr(false)
    const req = tab === 'coiled'
      ? fetchJSON('/api/market/coiled').then(d => { if (d.error) setErr(true); else setCoiled(d) })
      : tab === 'unbroken'
      ? fetchJSON('/api/market/unbroken').then(d => { if (d.error) setErr(true); else setUnbroken(d) })
      : tab === 'inst'
      ? fetchJSON('/api/market/inst-flow?top=40').then(d => { if (d.error) setErr(true); else setInst(d) })
      : tab === 'earnings'
      ? fetchJSON('/api/market/earnings?top=100').then(d => { if (d.error) setErr(true); else setEarnings(d) })
      : fetchJSON('/api/market/rankings?limit=100').then(d => { if (d.error) setErr(true); else setData(d) })
    req.catch(() => setErr(true)).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])
  // 切到蓄势/强势/机构/业绩 tab 时懒加载(服务端有缓存, 之后秒回)
  useEffect(() => { if ((tab === 'coiled' && !coiled) || (tab === 'unbroken' && !unbroken) || (tab === 'inst' && !inst) || (tab === 'earnings' && !earnings)) load() }, [tab])   // eslint-disable-line react-hooks/exhaustive-deps

  // ↑↓ 键在列表里快速翻K线(输入框聚焦时不劫持)
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
      const tag = (document.activeElement?.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return
      setSelected(prev => {
        if (!listRef.current?.length) return prev
        const arr = listRef.current
        const i = arr.findIndex(x => x.code === prev?.code)
        const ni = e.key === 'ArrowDown' ? Math.min(i + 1, arr.length - 1) : Math.max(i - 1, 0)
        return arr[ni] || prev
      })
      e.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const rawList = tab === 'coiled' ? (coiled?.rows || [])
    : tab === 'unbroken' ? (unbroken?.rows || [])
    : tab === 'inst' ? ((inst && inst[instSide]) || []).map(r => ({ ...r, pct: r['距最近上榜%'] }))
    : tab === 'earnings' ? (
        (earnSide === '持仓关联'
          ? [...(earnings?.['持仓关联预喜'] || []), ...(earnings?.['持仓关联预警'] || [])]
          : (earnings && earnings[earnSide]) || []
        ).map(r => ({ ...r, pct: r['幅度%'] }))
      )
    : ((data && data[tab]) || [])
  const list = board === '全部' ? rawList : rawList.filter(r => boardOf(r.code) === board)
  listRef.current = list

  return (
    <div className="bg-surface-2 border border-border rounded-xl overflow-hidden flex flex-col lg:flex-row h-[calc(100vh-11rem)] min-h-[480px]">
      <div className="lg:w-[420px] shrink-0 flex flex-col border-b lg:border-b-0 lg:border-r border-border min-h-0">
        <div className="flex items-center gap-1 px-3 py-2 border-b border-border-subtle">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`text-[12px] px-2 py-1 rounded border whitespace-nowrap shrink-0 ${tab === t.key ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {t.label}
            </button>
          ))}
          <span className="ml-auto text-[10px] text-text-muted whitespace-nowrap">{(tab === 'coiled' ? coiled?.as_of : data?.as_of)?.slice(5, 11) || ''}</span>
          <button onClick={load} title="刷新" className="text-[10.5px] px-1.5 py-0.5 rounded border border-border text-text-dim hover:text-text">刷新</button>
        </div>

        {/* 板块筛选 */}
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-subtle flex-wrap">
          {tab === 'inst' && (
            <>
              {[['net_buy', '机构净买入'], ['net_sell', '机构净卖出']].map(([k, lb]) => (
                <button key={k} onClick={() => setInstSide(k)}
                  className={`text-[11px] px-2 py-0.5 rounded ${instSide === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {lb}
                </button>
              ))}
              <span className="text-text-muted mx-0.5">·</span>
            </>
          )}
          {tab === 'earnings' && (
            <>
              {[['预喜', `预喜 ${earnings?.['n_预喜'] ?? ''}`], ['预警', `预警 ${earnings?.['n_预警'] ?? ''}`], ['持仓关联', '持仓关联']].map(([k, lb]) => (
                <button key={k} onClick={() => setEarnSide(k)}
                  className={`text-[11px] px-2 py-0.5 rounded ${earnSide === k ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
                  {lb}
                </button>
              ))}
              <span className="text-text-muted mx-0.5">·</span>
            </>
          )}
          {BOARDS.map(b => (
            <button key={b} onClick={() => setBoard(b)}
              className={`text-[11px] px-2 py-0.5 rounded ${board === b ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              {b}{b !== '全部' && rawList.length > 0 ? ` ${rawList.filter(r => boardOf(r.code) === b).length}` : ''}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {!loading && !err && list.length === 0 && (
            <div className="text-center py-8 text-text-dim text-[12px] px-4 leading-relaxed">
              {tab === 'coiled' ? '龙头池里今天没有满足"窄/平/静(高波动股按自身收敛度)、横盘≥45日且仍在箱体内"的蓄势基座（大波动市里稀缺属正常）' : `榜单 top100 里暂无${board}标的`}
            </div>
          )}
          {loading && <div className="text-center py-8 text-text-dim text-[12px]">{tab === 'coiled' ? '全市场扫描中…（首扫约20秒, 之后10分钟缓存秒开）' : '加载榜单…'}</div>}
          {err && <div className="text-center py-8 text-text-dim text-[12px]">榜单源暂不可达（东财抖动），<button onClick={load} className="text-accent">重试</button></div>}
          {!loading && !err && list.map((r, i) => {
            const active = selected?.code === r.code
            return (
              <button key={r.code} onClick={() => setSelected(r)} title={r['AI理由'] || undefined}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left border-b border-border-subtle/60 ${active ? 'bg-accent/15' : 'hover:bg-surface-3/60'}`}>
                <span className="text-[10px] font-mono text-text-muted w-5 shrink-0 text-right">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[12.5px] text-text-bright truncate">{r.name}</span>
                    {r.is_new && <span className="text-[8.5px] px-1 rounded bg-accent/15 text-accent shrink-0" title="上市前5日无涨跌幅限制">新</span>}
                    {r.is_st && <span className="text-[8.5px] px-1 rounded bg-bear/15 text-bear-bright shrink-0">ST</span>}
                  </span>
                  <span className="text-[10px] text-text-muted font-mono">
                    {boardOf(r.code)} · {r.code} · {tab === 'inst'
                      ? `净买 ${r['机构净买亿']}亿 · 上榜${r['上榜次数']}次`
                      : (r['行业'] || '—')}
                    {tab === 'earnings' && r['持仓关联'] && (
                      <span className="ml-1 px-1 rounded bg-accent/15 text-accent text-[9px]">{r['持仓关联']}</span>
                    )}
                  </span>
                  {tab === 'coiled' && r['业绩预告'] && (
                    <span className="block text-[9.5px] text-text-dim truncate">{r['业绩预告']}</span>
                  )}
                  {tab === 'unbroken' && (
                    <span className="block text-[9.5px] text-text-dim truncate">
                      {r['标签']}{r['业绩预告'] ? ` · ${r['业绩预告']}` : ''}
                    </span>
                  )}
                </span>
                <span className="text-right shrink-0">
                  <span className={`block text-[12.5px] font-mono font-semibold ${pctColor(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>
                  <span className="block text-[10px] text-text-muted font-mono">
                    {tab === 'coiled'
                      ? `${r['AI置信'] != null ? `AI${r['AI置信']}·` : ''}${r['标签'] || ''}·横盘${r['横盘日']}日`
                      : tab === 'unbroken'
                      ? `距高${r['距60日高%']}%·超额${r['近10日超额%'] >= 0 ? '+' : ''}${r['近10日超额%']}%`
                      : tab === 'inst'
                      ? `${(r['最近上榜'] || '').slice(5)}上榜·至今`
                      : tab === 'earnings'
                      ? `${r['类型']}·${(r['披露日'] || '').slice(5)}披露`
                      : tab === 'by_amount'
                      ? `${r['成交额亿']}亿`
                      : r.is_new ? '新股·无涨停'
                      : (r['涨停占比%'] != null ? `占停${r['涨停占比%']}%` : `量比${r['量比'] ?? '—'}`)}
                  </span>
                </span>
              </button>
            )
          })}

          {/* AI 淘汰的边缘候选: 折叠展示分数+判词, 供人工过目(不算正式结果) */}
          {tab === 'coiled' && !loading && !err && (coiled?.ai_dropped || []).length > 0 && (
            <div className="mt-1">
              <div className="px-3 py-1 text-[9.5px] text-text-muted border-t border-b border-border-subtle bg-surface-3/40">
                AI 看图判不符合({coiled.ai_dropped.length}) · 边缘候选仅供过目
              </div>
              {coiled.ai_dropped.map((r) => (
                <button key={r.code} onClick={() => setSelected(r)} title={r['AI理由'] || undefined}
                  className={`w-full flex items-center gap-2 px-3 py-1.5 text-left border-b border-border-subtle/40 opacity-55 hover:opacity-90 ${selected?.code === r.code ? 'bg-accent/10 opacity-90' : ''}`}>
                  <span className="min-w-0 flex-1">
                    <span className="text-[12px] text-text-dim truncate block">{r.name}</span>
                    <span className="text-[9.5px] text-text-muted font-mono">{r.code} · {r['行业'] || '—'}</span>
                    {r['业绩预告'] && <span className="block text-[9px] text-text-dim truncate">{r['业绩预告']}</span>}
                  </span>
                  <span className="text-right shrink-0">
                    <span className={`block text-[12px] font-mono ${pctColor(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>
                    <span className="block text-[9.5px] text-text-muted font-mono">AI{r['AI置信']}·横盘{r['横盘日']}日</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {tab === 'unbroken' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            结构完好观察池（"K线没砸下去"）：龙头池 → 距60日高回撤≤12%、近10日无单日大阴（≥6%）、上行结构未破位、近10日不明显跑输沪深300（同期{unbroken?.bench10 >= 0 ? '+' : ''}{unbroken?.bench10}%）· ↑↓ 键快速翻K线 · 结构完好只是当下事实，随时可能被砸 · 纯客观结构，非买卖建议
          </div>
        )}
        {tab === 'inst' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            近{inst?.window_days || 30}天龙虎榜机构专用席位统计（上榜日才披露，抽样非全量）· 主数字=现价较最近上榜日收盘的涨跌：净买入+至今大跌="机构接在山顶"，净卖出+至今大跌="机构跑对了" · 纯客观数字，非买卖建议
          </div>
        )}
        {tab === 'earnings' && !loading && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            最新报告期（{earnings?.period || '中报'}）业绩预告，全市场已披露 {earnings?.total ?? '—'} 家 · 主数字=归母净利同比变动中值% · 未披露≠业绩差（预告只对大幅变动强制），正式财报以披露日公告为准 · 持仓关联=直持或经由在持ETF前十大成分 · 纯客观数据，非买卖建议
          </div>
        )}
        {tab === 'coiled' && !loading && list.length > 0 && (
          <div className="shrink-0 px-3 py-1.5 border-t border-border-subtle text-[9.5px] text-text-muted leading-relaxed">
            蓄势观察池：40日箱体窄/平/静（高波动成长股按"比自己此前安静"的收敛度判）、横盘≥45日、仍在箱体内（已突破=偏晚不进正选）· AI看图复核、送审位按行业限流 · 中报预喜/预警=业绩预告凭据（未披露预告的显示最近一期实绩同比，预告只对大幅变动强制）· 横盘也可能向下解决 · 仅客观结构，非买卖建议
          </div>
        )}
      </div>

      <div className="flex-1 min-h-0 min-w-0">
        <StockPanel stock={selected} />
      </div>
    </div>
  )
}
