import Tooltip from '../Tooltip'
import { fmtMoney, fmtPct, priceColor } from '../../helpers'
import { FxHint, MarketChip, ProxyPulse, RowActions, TodayPulse, TypeChip, TypeMiniInfo, WeightBar } from './atoms'
import { TYPE_COLOR } from './constants'
import { formatCurrencyMoney } from './helpers'

// 持仓列表的一行。原来是主组件里一个 256 行的 renderRow 闭包 —— 它捕获了
// hoverId / insights / carryByCode / thesisCodes 和六个 handle* , 所以整段
// 只能待在主组件里, 而主组件因此没法读。
//
// 这些闭包变量按性质分成了三类 props:
//   - 行自己的: row / isLast / hovered / carry / hasThesis
//   - 跨行才算得出来的: totalMv(算占比) / insights(同源家族)
//   - 动作: actions 一个对象整体转给 RowActions(它的入参本来就是这几个)
export function HoldingRow({ row, isLast, hovered, onHover, totalMv, insights, carry, hasThesis, actions }) {
  return (
  <div
    onMouseEnter={() => onHover(row.id)}
    onMouseLeave={() => onHover(null)}
    className="licai-row px-3 md:px-6 py-[11px] items-center transition-colors"
    style={{
      borderBottom: isLast ? '1px solid var(--color-border)' : '1px solid var(--color-border-subtle)',
      background: hovered ? 'var(--color-surface-2)' : 'transparent',
    }}>
    <div className="flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center gap-1.5 min-w-0">
        <span className="text-[13px] font-semibold text-text-bright truncate">{row.name}</span>
        <TypeChip type={row.type} compact />
        {row.type === 'A' && <MarketChip market={row.extra?.market} />}
        {row.broker && (
          <span className="text-[10px] px-1.5 py-[1px] rounded bg-info/15 text-info border border-info/40 shrink-0 whitespace-nowrap font-medium">
            {row.broker}
          </span>
        )}
        {row.extra?.okxSynced && !row.extra?.okxStopped && (
          <Tooltip content="OKX 自动同步中">
            <span className="text-bull cursor-help text-[12px] leading-none">🔗</span>
          </Tooltip>
        )}
        {row.extra?.okxStopped && (
          <Tooltip content={
            <div>
              <div className="text-text-bright font-semibold mb-1">
                策略已停止{row.extra?.okxState ? ` (${row.extra.okxState})` : ''} · 待归档
              </div>
              <div>OKX 拉到的是结束时的最终数字, 不再变动; 钱已回到现货账户。</div>
              <div className="mt-1 text-text-dim text-[10.5px]">在这一行点「平仓归档」把盈亏定格转入已实现, 它就退出在持列表</div>
            </div>
          }>
            <span className="text-warn cursor-help text-[11px] leading-none px-1 rounded bg-warn/15 border border-warn/40 shrink-0 whitespace-nowrap">已停止</span>
          </Tooltip>
        )}
        {row.extra?.okxSynced === false && (
          <Tooltip content={
            <div>
              <div className="text-text-bright font-semibold mb-1">同步断连 · 数字为旧快照</div>
              <div>{row.extra?.syncError || 'OKX 不可达或凭证失效'}</div>
              <div className="mt-1 text-text-dim text-[10.5px]">盈亏非实时。常见原因: 代理软件没开 → 打开后到 设置→代理 点自动探测</div>
            </div>
          }>
            <span className="text-warn cursor-help text-[11px] leading-none px-1 rounded bg-warn/15 border border-warn/40 shrink-0">⚠ 同步断</span>
          </Tooltip>
        )}
        {insights.overlapRowIds.has(row.id) && (
          <Tooltip content={
            <div>
              <div className="text-text-bright font-semibold mb-1">同源风险家族</div>
              <div className="flex flex-col gap-0.5">
                {(insights.overlapByRow[row.id] || []).map(f => (
                  <div key={f.fam} className="text-text">
                    · {f.fam} 合计 {f.pct.toFixed(1)}%
                    {f.others.length > 0 && (
                      <span className="text-text-dim">
                        {' '}—— 还有 {f.others.slice(0, 3).join('、')}
                        {f.others.length > 3 ? ` 等${f.others.length}项` : ''}
                      </span>
                    )}
                  </div>
                ))}
              </div>
              <div className="text-text-dim mt-1.5 text-[10.5px] leading-snug">
                这几行是同一块风险(按穿透后的行业归的，不看名字)：分开看像分散，合起来才是真实敞口
              </div>
            </div>
          }>
            <span className="inline-flex items-center gap-0.5 text-[9.5px] font-semibold px-1 py-[1px] rounded shrink-0 cursor-help"
              style={{ color: '#e58a8a', background: '#e58a8a18', border: '1px solid #e58a8a40' }}>↔ 同源</span>
          </Tooltip>
        )}
      </div>
      <span className="font-mono text-[10px] text-text-muted truncate">{row.code}</span>
    </div>
    <div className="text-right flex flex-col items-end">
      <span className="font-mono text-[12.5px] text-text-bright tabular-nums">
        ¥{fmtMoney(row.mv)}
      </span>
      {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalMarketValue != null && (
        <FxHint extra={row.extra}>
          <span className="font-mono text-[10px] text-text-muted tabular-nums cursor-help">
            {formatCurrencyMoney(row.extra.currency, row.extra.originalMarketValue)}
          </span>
        </FxHint>
      )}
      {row.type === 'R' && row.extra?.okxTotalBudgetUsdt > 0 && row.extra?.okxAvailableUsdt != null && (
        <Tooltip content={
          <div className="leading-relaxed">
            <div className="text-text-bright font-semibold mb-1">
              马丁策略预算 {row.extra.okxBudgetSource === 'manual' ? '(手填)' : '(算法估算)'}
            </div>
            <div>首单 {row.extra.okxInitOrderAmt}U · 安全单基础 {row.extra.okxSafetyOrderAmt}U × {row.extra.okxMaxSafetyOrders} 档 · 量倍数 {row.extra.okxVolMult}</div>
            <div className="mt-1 text-[10.5px]">
              已投 <span className="text-text font-mono">{row.extra.okxInvestmentUsdt}U</span>
              <span className="mx-1">/</span>
              预算 <span className="text-text font-mono">{row.extra.okxTotalBudgetUsdt}U</span>
              <span className="mx-1">·</span>
              待投 <span className="text-bull-bright font-mono">{row.extra.okxAvailableUsdt}U</span>
            </div>
            {row.extra.okxBudgetSource !== 'manual' && (
              <div className="mt-1 text-[10px] text-warn">
                OKX raw 没"总预算"字段, 算法反推可能不准. 点击下面"改预算"用 OKX 客户端实际值覆盖.
              </div>
            )}
          </div>
        }>
          {/* 改马丁总预算: 写接口 + 重载资产的活儿留在主组件(actions.onEditBotBudget),
              这一行只负责"点了" —— 否则这个展示组件要自己拿 fetchJSON 和 setAssets。 */}
          <span
            onClick={(e) => { e.stopPropagation(); actions.onEditBotBudget?.(row) }}
            className="font-mono text-[10px] text-text-muted tabular-nums cursor-pointer hover:text-accent mt-0.5 whitespace-nowrap"
            title="点击改总预算">
            投 {row.extra.okxInvestmentUsdt}U / {row.extra.okxTotalBudgetUsdt}U <span className="text-bull-bright">余 {row.extra.okxAvailableUsdt}U</span>{row.extra.okxBudgetSource !== 'manual' && (
              <span className="text-warn ml-1" title="算法估算, 可能不准">~</span>
            )}
          </span>
        </Tooltip>
      )}
    </div>
    <div className="text-right flex flex-col items-end licai-md-only">
      <span className="font-mono text-[11px] text-text-dim tabular-nums">
        ¥{fmtMoney(row.cost)}
      </span>
      {row.extra?.currency && row.extra.currency !== 'CNY' && row.extra?.originalCostValue != null && (
        <FxHint extra={row.extra}>
          <span className="font-mono text-[9.5px] text-text-muted tabular-nums cursor-help">
            {formatCurrencyMoney(row.extra.currency, row.extra.originalCostValue)}
          </span>
        </FxHint>
      )}
    </div>
    <div className="text-right flex flex-col items-end">
      {row.type === 'M' && row.extra?.monthlyInterestEst ? (
        <Tooltip content={
          <div className="leading-relaxed">
            <div className="text-text-bright font-semibold mb-1">月息流估算</div>
            <div>当前余额 ¥{fmtMoney(row.mv)} × 年化 {(row.extra.annualYield * 100).toFixed(2)}% / 12</div>
            <div className="mt-1 text-[10.5px] text-text-dim">实际利率每天微变，估算 ±5% 误差</div>
            <div className="mt-1 text-[10.5px] text-bull-bright">
              日息 ≈ +¥{(row.extra.dailyInterestEst || 0).toFixed(2)} · 年息 ≈ +¥{fmtMoney(row.extra.yearlyInterestEst || 0)}
            </div>
          </div>
        }>
          <div className="flex flex-col items-end cursor-help">
            <span className="font-mono text-[12.5px] font-semibold tabular-nums text-bull-bright">
              ≈ +¥{fmtMoney(row.extra.monthlyInterestEst)}/月
            </span>
            <span className="font-mono text-[10px] text-text-dim">
              年化 {(row.extra.annualYield * 100).toFixed(2)}%
            </span>
          </div>
        </Tooltip>
      ) : (
        <>
          <span className={`font-mono text-[12.5px] font-semibold tabular-nums ${priceColor(row.pnl)}`}>
            {row.pnl != null ? (row.pnl >= 0 ? '+' : '') + fmtMoney(row.pnl) : '--'}
          </span>
          <span className={`font-mono text-[10px] opacity-85 ${priceColor(row.pnlPct)}`}>
            {row.pnlPct != null ? fmtPct(row.pnlPct) : ''}
          </span>
          {row.type === 'A' && Math.abs(carry || 0) > 0.5 && (() => {
            const total = (row.pnl || 0) + carry
            return (
              <Tooltip content="全周期真实盈亏 = 当前持仓浮动 + 历史已实现(清仓段/部分卖出)。清仓后又买回的票, 行内浮动只算这手, 这里把历史亏赚补回来。">
                <div className={`font-mono text-[9.5px] ${priceColor(total)} opacity-90 cursor-help`}>
                  真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                </div>
              </Tooltip>
            )
          })()}
          {row.type !== 'A' && row.type !== 'M' && Math.abs((row._raw?.realized_pnl || 0) - (row._raw?.cycle_realized || 0)) > 0.5 && (() => {
            const total = (row.pnl || 0) + (row._raw.realized_pnl || 0) - (row._raw.cycle_realized || 0)
            return (
              <Tooltip content="全周期真实盈亏 = 当前浮动 + 已实现(卖出/赎回的配对盈亏 + 利息分红)。卖掉部分的亏赚不在浮动里, 这里补齐——卖亏了再低位买回时, 浮动转正但真实口径仍记着那笔亏。">
                <div className={`font-mono text-[9.5px] ${priceColor(total)} opacity-90 cursor-help`}>
                  真实 {total >= 0 ? '+' : ''}{fmtMoney(total)}
                </div>
              </Tooltip>
            )
          })()}
        </>
      )}
    </div>
    <div className="text-right">
      {row.type === 'M' && row.extra?.dailyInterestEst ? (
        <Tooltip content={
          <div>
            <div className="text-text-bright font-semibold mb-1">日息估算</div>
            <div>当前余额 × 年化 / 365</div>
            <div className="mt-1 text-text-dim text-[10.5px]">货币基金每日结息</div>
          </div>
        }>
          <span className="font-mono text-[11px] text-bull-bright cursor-help">
            +¥{row.extra.dailyInterestEst.toFixed(2)}
          </span>
        </Tooltip>
      ) : row.type === 'F' && row.extra?.proxyChangePct != null ? (
        <ProxyPulse
          change={row.extra.proxyChangePct}
          label={row.extra.proxyLabel}
          details={row.extra.proxyDetails}
          fallbackToday={row.today} />
      ) : (
        <TodayPulse change={row.today} />
      )}
    </div>
    <div className="text-right licai-md-only">
      <WeightBar weight={row.mv / (totalMv || 1)} color={TYPE_COLOR[row.type]} />
    </div>
    <div className="relative pl-1 md:pl-2">
      {/* TypeMiniInfo: 桌面始终可见 (不再 hover 隐藏); 移动隐藏 */}
      <div className="hidden md:block">
        <TypeMiniInfo row={row} />
      </div>
      {/* RowActions: 桌面 hover 显示, 绝对覆盖右半. 容器 pointer-events-none 让事件
          穿透到 TypeMiniInfo (反推 tooltip 之类), 子元素重置 auto 接收点击.
          hover 时给个 surface-2 渐变遮罩, 与 row hover 背景色一致, 视觉自然. */}
      <div className="md:absolute md:inset-y-0 md:right-0 flex items-center md:pr-3 md:pointer-events-none [&>div]:pointer-events-auto transition-opacity"
        style={{
          background: hovered
            ? 'linear-gradient(to right, transparent 0%, var(--color-surface-2) 18%, var(--color-surface-2) 100%)'
            : 'transparent',
          transition: 'background .18s',
        }}>
        <RowActions row={row} visible={hovered} hasThesis={hasThesis} {...actions} />
      </div>
    </div>
  </div>
  )
}
