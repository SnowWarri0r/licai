// 从 Rankings.jsx 抽出 —— 榜单相关的纯函数, 供 Rankings 与其子组件共用。

export function pctColor(v) {
  if (v > 0) return 'text-bear'
  if (v < 0) return 'text-bull'
  return 'text-text-dim'
}

// 按代码前缀分板块。场内基金(1x/5x)不属于任何"板块", 单独标出 —— 588xxx 是科创板
// 主题 ETF, 标成「主板」是分类错误。榜单列表只有个股, 不受这条影响。
export function boardOf(code) {
  const c = String(code || '')
  if (/^[15]\d{5}$/.test(c)) return '场内基金'
  if (c.startsWith('688') || c.startsWith('689')) return '科创板'
  if (c.startsWith('30')) return '创业板'
  // 北交所: 8xxxxx(83/87/88) / 4xxxxx(老三板迁移) / 920xxx(新代码段) —— 与后端
  // market_data._is_bj_share 同口径; 漏掉 920 会把北交所票错标成主板
  if (c.startsWith('920') || c[0] === '8' || c[0] === '4') return '北交所'
  return '主板'
}

// 观察池只有两个"非真实分组": ALL 是跨组汇总视图, HELD 是持仓(现取, 清仓即消失)。
// 「自选」是**真实分组**, 跟「金矿」平级 —— 一只票可以同时在「自选」和「金矿」里。
// 它的特殊之处只有两点: 加进观察池时默认落它, 以及一个组都不剩时退回它。
export const WL_ALL = '全部'
export const WL_HELD = '持仓'
export const WL_DEFAULT = '自选'          // 默认组; 与后端 DEFAULT_WATCH_GROUP 同名
export const WL_LAST_KEY = 'licai.wl.group'
