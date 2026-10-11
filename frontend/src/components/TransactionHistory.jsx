import { useState, useEffect } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { bjToEtTime, etToBjTime, fmtPrice, loadBrokers, usTodayDate } from '../helpers'

const enc = (value) => encodeURIComponent(value)

const ACTION_TYPES = [
  { value: 'BUY', label: '买入' },
  { value: 'SELL', label: '卖出' },
  { value: 'ADD', label: '补仓(加仓)' },
  { value: 'REDUCE', label: '减仓' },
  { value: 'DIVIDEND', label: '现金分红' },
  { value: 'BONUS', label: '送股/转增' },
]
// 港美股另有拆股/合股: 价格栏填比例(1 拆 4 填 4, 10 合 1 填 0.1), 数量为 0, 不产生现金流
const SPLIT_TYPE = { value: 'SPLIT', label: '拆股/合股' }
const typesOf = (market) => market === 'A' ? ACTION_TYPES : [...ACTION_TYPES, SPLIT_TYPE]
const FX_HINT = '成交汇率(1 外币 = ? 人民币), 可选; 留空按成交日中行牌价自动取, 填结单上的汇率更准'

const ACQUIRE_TYPES = new Set(['BUY', 'ADD', 'BONUS'])

// 港美股流水按原币记: 价格、手续费都是港元/美元; 股数没有 100 股一手的规则
const marketOf = (code) => {
  const c = String(code || '').toUpperCase()
  return c.startsWith('HK.') ? 'HK' : c.startsWith('US.') ? 'US' : 'A'
}
const CCY = { A: '¥', HK: 'HK$', US: 'US$' }
// 美股: 日期 = 美东交易日(券商成交记录上那天), 时刻按美东录入/展示; 后端 trade_time 统一存北京时间
const US_DATE_HINT = '美东交易日 · 盈立成交记录上的日期; 北京时间凌晨成交算前一天'
const TIME_HINT = { A: '成交时刻(可选), 留空用录入时间, 供分时图打点', HK: '成交时刻(可选), 留空用录入时间, 供分时图打点',
  US: '美东成交时刻(可选, 如 09:35), 留空用录入时间, 供分时图打点' }
const defaultDate = (market) => market === 'US' ? usTodayDate() : new Date().toISOString().slice(0, 10)
// 展示/编辑用的时刻: 美股把北京时间换回美东(换不出来就原样)
const localTime = (market, date, t) => {
  if (!t) return t
  if (market !== 'US') return t
  return bjToEtTime(String(date || '').slice(0, 10), String(t)) || t
}
// 提交用: 美股把美东时刻换成北京时间
const storeTime = (market, date, t) => (market === 'US' && t && date) ? etToBjTime(date, t) : t
const FEE_HINT = {
  A: '按券商费率自动估算 (万1.854 / 5元起)',
  HK: '按港股收费方案自动估算(默认盈立 PRO: 佣金 0.06% + 平台费 18 港元/单 + 印花税等法定费用), 港元',
  US: '按美股收费方案自动估算(默认盈立 PRO: 佣金 0.01 美元/股、最低 1 美元 + 平台费 0.009 美元/股、最低 2.88 美元; 卖出另加 SEC/TAF), 美元',
}

function ActionRow({ action, editing, onSave, onCancel, onEdit, onDelete, brokers = [], market = 'A', code = '' }) {
  // 草稿里的 trade_time 是界面时区(美股=美东), 保存时再换回北京时间
  const toDraft = (a) => market === 'US' && a.trade_time
    ? { ...a, trade_time: localTime(market, a.trade_date, String(a.trade_time).slice(0, 5)) }
    : a
  const [draft, setDraft] = useState(() => toDraft(action))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { setDraft(toDraft(action)) }, [action, market])

  if (!editing) {
    const isAcquire = ACQUIRE_TYPES.has(action.action_type)
    const typeLabel = typesOf(market).find(t => t.value === action.action_type)?.label || action.action_type
    const isSplit = action.action_type === 'SPLIT'
    const feeOverride = action.fee != null   // 用户手填了 override
    return (
      <tr className="border-t border-border-subtle hover:bg-surface-2/30">
        <td className="py-1.5 px-2 text-text-muted">{action.trade_date || '--'}
          {action.at_time && <span className="block text-[10px] text-text-dim font-mono">{String(localTime(market, action.trade_date, action.at_time)).slice(0, 5)}{market === 'US' && <span className="text-text-muted ml-0.5" title="美东时间">美东</span>}{!action.trade_time && <span title="按录入时间推断">~</span>}</span>}
        </td>
        <td className={`py-1.5 px-2 text-[11px] ${isAcquire ? 'text-bull' : 'text-bear'}`}>{typeLabel}
          {action.broker_effective && <span className="block text-[10px] text-text-dim">{action.broker_effective}{!action.broker && <span title="用持仓默认券商">~</span>}</span>}
        </td>
        <td className="py-1.5 px-2 text-right font-mono">{isSplit && market !== 'A' ? `×${action.price}` : fmtPrice(action.price, code)}
          {market !== 'A' && action.fx_rate > 0 && <span className="block text-[10px] text-text-dim" title="成交汇率(1 外币 = ? 人民币)">汇 {Number(action.fx_rate).toFixed(4)}</span>}
        </td>
        <td className="py-1.5 px-2 text-right font-mono">{isSplit && market !== 'A' ? '--' : action.shares}</td>
        <td className="py-1.5 px-2 text-right font-mono text-[11px]"
          title={feeOverride ? '已手填覆盖' : FEE_HINT[market]}>
          {action.fee_effective != null ? `${CCY[market]}${action.fee_effective.toFixed(2)}` : '--'}
          {feeOverride && <span className="text-accent ml-0.5">·</span>}
        </td>
        <td className="py-1.5 px-2 text-[11px] text-text-muted">{action.note || '--'}</td>
        <td className="py-1.5 px-2 text-center">
          <button onClick={() => onEdit()} className="text-[11px] text-accent hover:underline cursor-pointer mr-2">编辑</button>
          <button onClick={() => onDelete()} className="text-[11px] text-bear hover:underline cursor-pointer">删除</button>
        </td>
      </tr>
    )
  }

  // draft.fee: null/undefined = 自动估; '' = 显式清空; 数字 = override
  const feeInput = draft.fee == null ? '' : String(draft.fee)
  const onFeeChange = (v) => setDraft({ ...draft, fee: v === '' ? null : (parseFloat(v) || 0), fee_set: true })

  return (
    <tr className="border-t border-border-subtle bg-surface-3/40">
      <td className="py-1.5 px-2">
        <input type="date" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-32" value={draft.trade_date || ''} onChange={e => setDraft({ ...draft, trade_date: e.target.value })} title={market === 'US' ? US_DATE_HINT : undefined} />
        <input type="time" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-24 mt-1 block text-text-dim" value={draft.trade_time || ''} onChange={e => setDraft({ ...draft, trade_time: e.target.value })} title={TIME_HINT[market]} />
      </td>
      <td className="py-1.5 px-2">
        <select className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px]" value={draft.action_type} onChange={e => setDraft({ ...draft, action_type: e.target.value })}>
          {typesOf(market).map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <select className="bg-bg border border-border rounded px-1.5 py-0.5 text-[11px] mt-1 block text-text-dim w-full" value={draft.broker || ''} onChange={e => setDraft({ ...draft, broker: e.target.value })} title="本笔券商, 留空用持仓默认">
          <option value="">默认券商</option>
          {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
        </select>
      </td>
      <td className="py-1.5 px-2"><input type="number" step="0.0001" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" value={draft.price} onChange={e => setDraft({ ...draft, price: parseFloat(e.target.value) || 0 })} title={draft.action_type === 'SPLIT' ? '拆股比例: 1 拆 4 填 4, 10 合 1 填 0.1' : undefined} />
        {market !== 'A' && draft.action_type !== 'SPLIT' && <input type="number" step="0.0001" min="0" placeholder="汇率" title={FX_HINT}
          className="bg-bg border border-border rounded px-1.5 py-0.5 text-[11px] w-20 text-right font-mono mt-1 block text-text-dim"
          value={draft.fx_rate ?? ''} onChange={e => setDraft({ ...draft, fx_rate: e.target.value === '' ? null : (parseFloat(e.target.value) || 0), fx_set: true })} />}
      </td>
      <td className="py-1.5 px-2"><input type="number" step={market === 'A' ? 100 : 1} min={market === 'A' ? 100 : 1} className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" value={draft.shares} onChange={e => setDraft({ ...draft, shares: parseInt(e.target.value) || 0 })} /></td>
      <td className="py-1.5 px-2">
        <input type="number" step="0.01" min="0"
          placeholder={action.fee_auto != null ? `估 ${action.fee_auto.toFixed(2)}` : '0'}
          className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono"
          value={feeInput} onChange={e => onFeeChange(e.target.value)}
          title={`留空 = 用券商费率自动估算; 填值 = 覆盖(单位 ${CCY[market]})`} />
      </td>
      <td className="py-1.5 px-2"><input type="text" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-full" placeholder="备注" value={draft.note || ''} onChange={e => setDraft({ ...draft, note: e.target.value })} /></td>
      <td className="py-1.5 px-2 text-center">
        <button onClick={() => onSave(draft)} className="text-[11px] text-bull hover:underline cursor-pointer mr-2">保存</button>
        <button onClick={() => onCancel()} className="text-[11px] text-text-dim hover:underline cursor-pointer">取消</button>
      </td>
    </tr>
  )
}

export default function TransactionHistory({ stockCode, stockName, onClose, onChange }) {
  const market = marketOf(stockCode)
  const [actions, setActions] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)
  const [adding, setAdding] = useState(false)
  const [brokers, setBrokers] = useState([])
  useEffect(() => { loadBrokers().then(setBrokers) }, [])
  const [newAction, setNewAction] = useState({
    action_type: 'BUY',
    broker: '',
    price: '',
    shares: '',
    fee: '',
    trade_date: defaultDate(market),
    trade_time: '',     // 可选 HH:MM 成交时刻(美股按美东); 留空走录入时间, 供分时图打点
    note: '',
  })

  const load = async () => {
    try {
      const list = await fetchJSON(`/api/portfolio/${enc(stockCode)}/actions`)
      // 按 日期+成交时刻 升序(同一天也要按时分排, 之前只按 trade_date 天粒度 → 同日乱序)
      // at_time 缺失时退 created_at 的时间, 再退 id, 保证稳定的时间顺序
      // 美股: at_time 是北京时间, 一个美东交易日跨北京午夜, 要换成美东再排
      const key = a => `${a.trade_date || ''} ${localTime(market, a.trade_date, a.at_time) || (a.created_at || '').slice(11) || ''}`
      list.sort((a, b) => key(a).localeCompare(key(b)) || ((a.id || 0) - (b.id || 0)))
      setActions(list)
    } finally {
      setLoading(false)
    }
  }
  // 只按 stockCode 取。load 是每次渲染新建的普通函数, 列进依赖会无限重取。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [stockCode])

  const handleAdd = async () => {
    const isSplit = newAction.action_type === 'SPLIT'
    if (!newAction.price || (!isSplit && !newAction.shares) || !newAction.trade_date) return alert('请填完整')
    const body = {
      ...newAction,
      trade_time: storeTime(market, newAction.trade_date, newAction.trade_time),
      price: parseFloat(newAction.price),
      shares: isSplit ? 0 : parseInt(newAction.shares),
    }
    if (market !== 'A' && !isSplit && parseFloat(newAction.fx_rate) > 0) body.fx_rate = parseFloat(newAction.fx_rate)
    else delete body.fx_rate
    if (newAction.fee != null && newAction.fee !== '') {
      body.fee = parseFloat(newAction.fee)
    } else {
      delete body.fee
    }
    await fetchJSON(`/api/portfolio/${enc(stockCode)}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    setAdding(false)
    setNewAction({ action_type: 'BUY', broker: '', price: '', shares: '', trade_date: defaultDate(market), trade_time: '', note: '', fee: '', fx_rate: '' })
    await load()
    onChange?.()
  }

  const handleSave = async (draft) => {
    const body = {
      action_type: draft.action_type,
      price: parseFloat(draft.price),
      shares: parseInt(draft.shares),
      trade_date: draft.trade_date,
      trade_time: storeTime(market, draft.trade_date, draft.trade_time) || '',   // "" → 清空回退录入时间; 美股美东→北京
      broker: draft.broker || '',           // "" → 用持仓默认券商
      note: draft.note || '',
    }
    // 改了汇率: 填值 = 按结单汇率; 清空(传 0) = 回退按成交日牌价自动取
    if (draft.fx_set) body.fx_rate = draft.fx_rate > 0 ? draft.fx_rate : 0
    if (draft.action_type === 'SPLIT') body.shares = 0
    // 用户改了 fee (fee_set 标记): 显式传 fee (null=清空回退自动估)
    if (draft.fee_set) {
      body.fee = draft.fee
      body.fee_set = true
    }
    await fetchJSON(`/api/portfolio/actions/${draft.id}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    })
    setEditingId(null)
    await load()
    onChange?.()
  }

  const handleDelete = async (id) => {
    if (!confirm('确定删除这条记录？会重新计算持仓成本。')) return
    await fetchJSON(`/api/portfolio/actions/${id}`, { method: 'DELETE' })
    await load()
    onChange?.()
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}>
      <div className="bg-surface border border-border rounded-xl p-5 w-[720px] max-w-[95vw] max-h-[85vh] overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}>

        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[15px] font-semibold text-text-bright">
            交易历史 — {stockName} <span className="text-[12px] font-mono text-text-dim">({stockCode})</span>
          </h3>
          <button onClick={onClose} className="text-text-dim hover:text-text cursor-pointer">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="text-center text-text-dim py-6">加载中...</div>
          ) : (
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-text-dim text-[11px] sticky top-0 bg-surface">
                  <th className="py-2 px-2 text-left font-normal" title={market === 'US' ? US_DATE_HINT : undefined}>{market === 'US' ? '美东交易日' : '日期'}</th>
                  <th className="py-2 px-2 text-left font-normal">类型</th>
                  <th className="py-2 px-2 text-right font-normal">价格</th>
                  <th className="py-2 px-2 text-right font-normal">数量</th>
                  <th className="py-2 px-2 text-right font-normal" title="手续费 (自动按券商费率估; 编辑可填实际值覆盖)">手续费</th>
                  <th className="py-2 px-2 text-left font-normal">备注</th>
                  <th className="py-2 px-2 text-center font-normal w-24">操作</th>
                </tr>
              </thead>
              <tbody>
                {actions.map(a => (
                  <ActionRow
                    market={market}
                    code={stockCode}
                    key={a.id}
                    action={a}
                    editing={editingId === a.id}
                    onSave={handleSave}
                    onCancel={() => setEditingId(null)}
                    onEdit={() => setEditingId(a.id)}
                    onDelete={() => handleDelete(a.id)}
                    brokers={brokers}
                  />
                ))}

                {adding && (
                  <tr className="border-t border-border-subtle bg-bull-bg">
                    <td className="py-1.5 px-2">
                      <input type="date" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-32" value={newAction.trade_date} onChange={e => setNewAction({ ...newAction, trade_date: e.target.value })} title={market === 'US' ? US_DATE_HINT : undefined} />
                      <input type="time" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-24 mt-1 block text-text-dim" value={newAction.trade_time} onChange={e => setNewAction({ ...newAction, trade_time: e.target.value })} title={TIME_HINT[market]} />
                    </td>
                    <td className="py-1.5 px-2">
                      <select className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px]" value={newAction.action_type} onChange={e => setNewAction({ ...newAction, action_type: e.target.value })}>
                        {typesOf(market).map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                      <select className="bg-bg border border-border rounded px-1.5 py-0.5 text-[11px] mt-1 block text-text-dim w-full" value={newAction.broker} onChange={e => setNewAction({ ...newAction, broker: e.target.value })} title="本笔券商, 留空用持仓默认">
                        <option value="">默认券商</option>
                        {brokers.map(b => <option key={b.id} value={b.name}>{b.name}</option>)}
                      </select>
                    </td>
                    <td className="py-1.5 px-2"><input type="number" step="0.0001" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" placeholder={newAction.action_type === 'SPLIT' ? '比例' : '价格'} value={newAction.price} onChange={e => setNewAction({ ...newAction, price: e.target.value })} title={newAction.action_type === 'SPLIT' ? '拆股比例: 1 拆 4 填 4, 10 合 1 填 0.1' : undefined} />
                      {market !== 'A' && newAction.action_type !== 'SPLIT' && <input type="number" step="0.0001" min="0" placeholder="汇率(可选)" title={FX_HINT}
                        className="bg-bg border border-border rounded px-1.5 py-0.5 text-[11px] w-20 text-right font-mono mt-1 block text-text-dim"
                        value={newAction.fx_rate ?? ''} onChange={e => setNewAction({ ...newAction, fx_rate: e.target.value })} />}
                    </td>
                    <td className="py-1.5 px-2">{newAction.action_type === 'SPLIT' ? <span className="text-[11px] text-text-dim">--</span>
                      : <input type="number" step={market === 'A' ? 100 : 1} min={market === 'A' ? 100 : 1} className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" placeholder="数量" value={newAction.shares} onChange={e => setNewAction({ ...newAction, shares: e.target.value })} />}</td>
                    <td className="py-1.5 px-2"><input type="number" step="0.01" min="0" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-20 text-right font-mono" placeholder="自动估" value={newAction.fee ?? ''} onChange={e => setNewAction({ ...newAction, fee: e.target.value })} title={`留空 = 按券商费率自动估; 填值 = 实际手续费(单位 ${CCY[market]})`} /></td>
                    <td className="py-1.5 px-2"><input type="text" className="bg-bg border border-border rounded px-1.5 py-0.5 text-[12px] w-full" placeholder="备注" value={newAction.note} onChange={e => setNewAction({ ...newAction, note: e.target.value })} /></td>
                    <td className="py-1.5 px-2 text-center">
                      <button onClick={handleAdd} className="text-[11px] text-bull hover:underline cursor-pointer mr-2">确认</button>
                      <button onClick={() => setAdding(false)} className="text-[11px] text-text-dim hover:underline cursor-pointer">取消</button>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>

        <div className="flex items-center justify-between pt-3 border-t border-border mt-2">
          <div className="text-[11px] text-text-muted">
            {actions.length} 条记录 · 每次修改会自动按 FIFO 重算持仓均价
            {market === 'US' && <span className="block text-[10.5px]">日期/时刻按美东填: 盈立成交记录上的日期; 北京时间凌晨成交算前一天</span>}
          </div>
          {!adding && (
            <button onClick={() => setAdding(true)}
              className="text-[12px] px-3 py-1 rounded bg-accent/10 text-accent hover:bg-accent/20 cursor-pointer">
              + 添加记录
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
