import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import ProKline from './ProKline'
import StockAskModal from './StockAskModal'

const TABS = [
  { key: 'gainers', label: '涨幅榜' },
  { key: 'by_amount', label: '成交额榜' },
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
  const [tab, setTab] = useState('gainers')
  const [board, setBoard] = useState('全部')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(false)
  const [selected, setSelected] = useState(null)

  const load = () => {
    setLoading(true); setErr(false)
    fetchJSON('/api/market/rankings?limit=100')
      .then(d => { if (d.error) { setErr(true) } else setData(d) })
      .catch(() => setErr(true))
      .finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const rawList = (data && data[tab]) || []
  const list = board === '全部' ? rawList : rawList.filter(r => boardOf(r.code) === board)

  return (
    <div className="bg-surface-2 border border-border rounded-xl overflow-hidden flex flex-col lg:flex-row h-[calc(100vh-11rem)] min-h-[480px]">
      <div className="lg:w-[420px] shrink-0 flex flex-col border-b lg:border-b-0 lg:border-r border-border min-h-0">
        <div className="flex items-center gap-1 px-3 py-2 border-b border-border-subtle">
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)}
              className={`text-[12px] px-2.5 py-1 rounded border ${tab === t.key ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
              {t.label}
            </button>
          ))}
          <span className="ml-auto text-[10px] text-text-muted">{data?.as_of ? data.as_of.slice(5) : ''}</span>
          <button onClick={load} title="刷新" className="text-[10.5px] px-1.5 py-0.5 rounded border border-border text-text-dim hover:text-text">刷新</button>
        </div>

        {/* 板块筛选 */}
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border-subtle flex-wrap">
          {BOARDS.map(b => (
            <button key={b} onClick={() => setBoard(b)}
              className={`text-[11px] px-2 py-0.5 rounded ${board === b ? 'bg-accent/15 text-accent' : 'text-text-dim hover:text-text'}`}>
              {b}{b !== '全部' && rawList.length > 0 ? ` ${rawList.filter(r => boardOf(r.code) === b).length}` : ''}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto min-h-0">
          {!loading && !err && list.length === 0 && <div className="text-center py-8 text-text-dim text-[12px]">榜单 top100 里暂无{board}标的</div>}
          {loading && <div className="text-center py-8 text-text-dim text-[12px]">加载榜单…</div>}
          {err && <div className="text-center py-8 text-text-dim text-[12px]">榜单源暂不可达（东财抖动），<button onClick={load} className="text-accent">重试</button></div>}
          {!loading && !err && list.map((r, i) => {
            const active = selected?.code === r.code
            return (
              <button key={r.code} onClick={() => setSelected(r)}
                className={`w-full flex items-center gap-2 px-3 py-1.5 text-left border-b border-border-subtle/60 ${active ? 'bg-accent/15' : 'hover:bg-surface-3/60'}`}>
                <span className="text-[10px] font-mono text-text-muted w-5 shrink-0 text-right">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[12.5px] text-text-bright truncate">{r.name}</span>
                    {r.is_new && <span className="text-[8.5px] px-1 rounded bg-accent/15 text-accent shrink-0" title="上市前5日无涨跌幅限制">新</span>}
                    {r.is_st && <span className="text-[8.5px] px-1 rounded bg-bear/15 text-bear-bright shrink-0">ST</span>}
                  </span>
                  <span className="text-[10px] text-text-muted font-mono">{boardOf(r.code)} · {r.code} · {r['行业'] || '—'}</span>
                </span>
                <span className="text-right shrink-0">
                  <span className={`block text-[12.5px] font-mono font-semibold ${pctColor(r.pct)}`}>{r.pct >= 0 ? '+' : ''}{r.pct}%</span>
                  <span className="block text-[10px] text-text-muted font-mono">
                    {tab === 'by_amount'
                      ? `${r['成交额亿']}亿`
                      : r.is_new ? '新股·无涨停'
                      : (r['涨停占比%'] != null ? `占停${r['涨停占比%']}%` : `量比${r['量比'] ?? '—'}`)}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="flex-1 min-h-0 min-w-0">
        <StockPanel stock={selected} />
      </div>
    </div>
  )
}
