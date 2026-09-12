import { useEffect, useState } from 'react'
import { fetchJSON } from '../../hooks/useApi'

/* OKX 策略之外的现货余额。
   为什么不自动建仓: 余额只给数量, 给不出成本 —— 凭空建一笔成本会让盈亏从第一天就错。
   所以这里只列差异, 建/更新点一下确认(建仓成本按当前市值入账, 之后可以自己改)。
   为什么只取"可用+资金账户": 交易账户余额里的 frozenBal 是被网格/马丁占用的钱, 已经
   作为 BOT 资产在跟踪了, 再算一遍就是同一笔钱数两遍(实测 BTC 99.97% 都是 frozen)。 */
export function OkxSpotSection() {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState('')

  const load = () => fetchJSON('/api/assets/okx/spot')
    .then(setD).catch(e => setMsg(String(e.message || e)))
  useEffect(() => { load() }, [])

  const sync = async (row) => {
    setBusy(row.ccy); setMsg('')
    try {
      if (row.action === 'create') {
        await fetchJSON('/api/assets', {
          method: 'POST',
          body: JSON.stringify({
            asset_type: 'CRYPTO', code: `${row.ccy}-USDT`, name: `${row.ccy} 现货`,
            platform: 'OKX', shares: row.qty,
            // 成本未知 → 以当前市值入账, 盈亏从同步这一刻起算
            cost_amount: row.cny,
            note: 'OKX 现货同步(成本按同步时市值入账, 可自行修正)',
          }),
        })
      } else {
        await fetchJSON(`/api/assets/${row.tracked_id}`, {
          method: 'PUT', body: JSON.stringify({ shares: row.qty }),
        })
      }
      setMsg(`${row.ccy} 已${row.action === 'create' ? '建仓' : '更新数量'}`)
      await load()
    } catch (e) { setMsg(String(e.message || e)) } finally { setBusy('') }
  }

  const rows = d?.spot || []
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">
          OKX 策略之外的现货
          {d && <span className="ml-2 text-[11px] font-normal text-text-muted">
            可同步 {rows.length} 种{d.dust?.n ? ` · 尘埃 ${d.dust.n} 种已忽略` : ''}
          </span>}
        </label>
        <button onClick={load}
          className="text-[11px] px-2 py-0.5 rounded border border-border text-text-dim hover:text-text cursor-pointer">
          重新读取
        </button>
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        只读 <code className="bg-surface-3 px-1 rounded">可用余额 + 资金账户</code>，
        <b>不含被策略冻结的部分</b>——那笔钱已经作为机器人资产在跟踪，重复计入会让总资产虚高。
        余额只有数量没有成本，所以建仓/更新需要你点一下确认。
      </p>
      {rows.length === 0 && d && (
        <p className="text-[11px] text-text-muted">策略之外没有可同步的现货（低于 1 美元的尘埃已忽略）。</p>
      )}
      {rows.map(r => (
        <div key={r.ccy} className="flex items-center gap-2 mb-1.5 text-[11.5px]">
          <span className="font-mono text-text-bright w-14">{r.ccy}</span>
          <span className="font-mono text-text-dim w-32">{r.qty}</span>
          <span className="font-mono text-text-muted w-20">¥{r.cny}</span>
          <span className="text-text-muted">
            {r.action === 'create' ? '看板未跟踪'
              : r.action === 'ok' ? '数量一致'
              : `看板 ${r.tracked_shares} → 差 ${r.qty_diff}`}
          </span>
          {r.action !== 'ok' && (
            <button onClick={() => sync(r)} disabled={busy === r.ccy}
              className="ml-auto text-[11px] px-2 py-0.5 rounded bg-accent/20 text-accent border border-accent/40 hover:bg-accent/30 disabled:opacity-50 cursor-pointer">
              {busy === r.ccy ? '同步中…' : (r.action === 'create' ? '按现价建仓' : '更新数量')}
            </button>
          )}
        </div>
      ))}
      {(d?.frozen || []).length > 0 && (
        <details className="mt-2">
          <summary className="text-[11px] text-text-muted cursor-pointer">
            被策略冻结 ¥{((d.frozen_usd || 0) * (d.usdcny || 7.2)).toFixed(0)}（已在机器人资产里，不重复计）
          </summary>
          {d.frozen.map(f => (
            <div key={f.ccy} className="text-[11px] text-text-dim mt-1 font-mono">
              {f.ccy} {f.qty} · ¥{f.cny}
            </div>
          ))}
          {d.strategy_idle && (
            <div className="text-[11px] text-warn mt-1.5 leading-snug">
              其中约 ¥{d.strategy_idle.cny} 是策略<b>预留但还没投进去</b>的现金
              （冻结 {d.frozen_usd} USD − 两个策略市值 {d.bot_value_usd} USD）——
              这部分是你的钱，但看板目前没算进总资产。
            </div>
          )}
        </details>
      )}
      {msg && <p className="text-[11px] text-text-dim mt-1.5">{msg}</p>}
    </div>
  )
}
