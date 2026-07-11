import { useState, useEffect, useCallback } from 'react'
import { fetchJSON } from '../hooks/useApi'
import SkeletonCard from './Skeleton'

// ETF 题材透视(避雷雷达): 季报真实成分 vs 名称宣称主题。
// 警示配色: 贴题=bull, 有偏离=warn, 偏离显著=bear, 宽基/风格/跨境=中性
const BADGE = {
  '贴题': 'bg-bull/15 text-bull-bright border-bull/40',
  '有偏离': 'bg-warn/15 text-warn border-warn/40',
  '偏离显著': 'bg-bear/15 text-bear-bright border-bear/40',
}
const QUICK = ['红利', '家电', '半导体', '通信', '军工', '创新药', '白酒', '证券', '机器人', '人工智能']

function EtfBlock({ x }) {
  const badgeCls = BADGE[x['警示']] || 'bg-surface-3 text-text-dim border-border-med'
  const dist = x['行业分布'] || []
  const maxW = Math.max(...dist.map(d => d['权重%']), 1)
  // 该行业下有任一前十大成分贴题 → 行业条按贴题色
  const matchedInds = new Set((x['前十大'] || []).filter(t => t['贴题']).map(t => t['行业']))
  return (
    <div className="border border-border-subtle rounded-lg p-3 mb-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[13px] font-semibold text-text-bright">{x.name}</span>
        <span className="text-[10.5px] font-mono text-text-muted">{x.code}</span>
        {x['规模亿'] != null && <span className="text-[10.5px] text-text-dim">规模 {x['规模亿']} 亿</span>}
        {x['季报'] && <span className="text-[10px] text-text-muted">{x['季报']}</span>}
        <span className={`ml-auto text-[10px] px-1.5 py-[2px] rounded border ${badgeCls}`}>
          {x['警示'] || x['主题类型']}
        </span>
        {x['主题匹配权重%'] != null && (
          <span className="text-[11.5px] font-mono font-semibold text-text-bright">
            贴题 {x['主题匹配权重%']}%
          </span>
        )}
      </div>
      {x.note && <div className="text-[10.5px] text-text-muted mt-1">{x.note}</div>}

      {dist.length > 0 && (
        <div className="mt-2 space-y-1">
          {dist.slice(0, 6).map(d => (
            <div key={d['行业']} className="flex items-center gap-2">
              <span className="text-[10.5px] text-text-dim w-24 shrink-0 truncate text-right">{d['行业']}</span>
              <div className="flex-1 h-2 rounded bg-surface-3 overflow-hidden">
                <div className="h-full rounded"
                  style={{ width: `${d['权重%'] / maxW * 100}%`,
                    background: matchedInds.has(d['行业']) ? 'var(--color-bull)' : 'var(--color-border-strong)' }} />
              </div>
              <span className="text-[10px] font-mono text-text-muted w-11 shrink-0">{d['权重%']}%</span>
            </div>
          ))}
        </div>
      )}

      {(x['前十大'] || []).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {x['前十大'].map(t => (
            <span key={t.code} title={`${t['行业']} · 市值${t['市值亿']}亿`}
              className={`text-[10px] px-1.5 py-[2px] rounded border ${
                t['贴题'] === false ? 'border-bear/40 text-bear-bright bg-bear/10' : 'border-border-subtle text-text-dim'}`}>
              {t.name} {t['权重%']}%{t['贴题'] === false ? ' ✗' : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function EtfXray() {
  const [tab, setTab] = useState('mine')      // mine | theme
  const [theme, setTheme] = useState('')
  const [input, setInput] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const load = useCallback(async (t, th) => {
    setLoading(true); setErr('')
    try {
      const url = t === 'mine' ? '/api/market/etf-xray/mine'
        : `/api/market/etf-xray/theme?theme=${encodeURIComponent(th)}&top=5`
      const d = await fetchJSON(url)
      if (d.error) setErr(d.error)
      setData(d)
    } catch (e) { setErr(e?.message || '加载失败') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { if (tab === 'mine') load('mine') }, [tab, load])

  const goTheme = (th) => {
    const t = (th || '').trim()
    if (!t) return
    setTab('theme'); setTheme(t); setInput(t); load('theme', t)
  }

  return (
    <div className="bg-surface-2 border border-border rounded-xl p-4 md:p-5">
      <div className="flex items-center gap-2 mb-2 flex-wrap">
        <h3 className="text-[14px] font-semibold text-text-bright m-0">ETF 题材透视</h3>
        <span className="text-[10.5px] text-text-muted">季报真实成分 vs 名称主题 · 避雷挂羊头</span>
        <div className="flex gap-1 ml-auto">
          <button onClick={() => setTab('mine')}
            className={`text-[11px] px-2 py-0.5 rounded border ${tab === 'mine' ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
            我的ETF
          </button>
          <button onClick={() => { setTab('theme'); if (theme) load('theme', theme) }}
            className={`text-[11px] px-2 py-0.5 rounded border ${tab === 'theme' ? 'bg-accent/20 text-accent border-accent/40' : 'bg-surface-3 text-text-dim border-transparent hover:text-text'}`}>
            查主题
          </button>
        </div>
      </div>

      {tab === 'theme' && (
        <div className="mb-3">
          <div className="flex gap-2 mb-1.5">
            <input value={input} onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) goTheme(input) }}
              placeholder="输入主题词, 如 红利 / 家电 / 半导体"
              className="flex-1 text-[12px] px-3 py-1.5 rounded-lg bg-surface-3 border border-border text-text placeholder:text-text-muted focus:border-accent/50 outline-none" />
            <button onClick={() => goTheme(input)} disabled={!input.trim()}
              className="text-[12px] px-3 py-1.5 rounded-lg bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-40">
              透视
            </button>
          </div>
          <div className="flex flex-wrap gap-1">
            {QUICK.map(q => (
              <button key={q} onClick={() => goTheme(q)}
                className={`text-[10.5px] px-2 py-[2px] rounded-full border cursor-pointer ${theme === q ? 'border-accent text-accent bg-accent/10' : 'border-border-med text-text-dim hover:text-text'}`}>
                {q}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading && <SkeletonCard bare rows={6} label={tab === 'mine' ? '透视在持 ETF…（季报+行业映射, 首次约 10-30 秒）' : `透视 ${theme} 主题…`} />}
      {!loading && err && <div className="text-center py-4 text-bear text-[12px]">{err}</div>}
      {!loading && !err && data && (
        <>
          {tab === 'theme' && data['总候选'] != null && (
            <div className="text-[11px] text-text-muted mb-2">
              名称含「{data.theme}」共 {data['总候选']} 只 · 只对比规模最大的 {data.rows?.length} 只
            </div>
          )}
          {(data.rows || []).map(x => <EtfBlock key={x.code} x={x} />)}
          {(data.rows || []).length === 0 && <div className="text-center py-4 text-text-dim text-[12px]">{data.note || '无数据'}</div>}
        </>
      )}
      <div className="text-[10px] text-text-muted pt-2 mt-1 border-t border-border-subtle">
        成分数据 = 基金季报（滞后至上一季度末，非实时）· 贴题% = 成分权重中命中主题行业的占比 · 宽基/风格类不适用行业口径 · 纯客观结构，不构成任何买卖建议
      </div>
    </div>
  )
}
