export const TYPE_META = {
  A: { label: '股票',   short: '股', tintVar: '--color-accent',     desc: 'A股 / 港股 / 美股' },
  F: { label: '基金',   short: '基', tintVar: '--color-info',       desc: '公募 / ETF / LOF' },
  W: { label: '理财',   short: '理', tintVar: '--color-bull',       desc: '银证理财 (T+30 锁定)' },
  M: { label: '现金',   short: '现', tintVar: '--color-text-dim',   desc: 'T+0 货币基金 / 银行活期' },
  C: { label: '加密',   short: '加', tintVar: '--color-warn',       desc: 'BTC / ETH / …' },
  R: { label: '机器人', short: '量', tintVar: '--color-text-dim',   desc: '量化 / 跟投' },
}

// Fallback color hex for donut strokes (Tailwind arbitrary values can't use var() easily)
export const TYPE_COLOR = {
  A: '#c8a876', // accent (sand)
  F: '#85a0b4', // info (slate blue)
  W: '#5fa86c', // bull (理财稳定收益绿)
  M: '#7a9b8e', // sage teal (现金/T+0 流动性)
  C: '#d4a05c', // warn (amber)
  R: '#8a8378', // text-dim grey
}

export const TYPE_ORDER = ['A', 'F', 'W', 'M', 'C', 'R']

export const ASSET_TYPE_TO_KEY = { FUND: 'F', CRYPTO: 'C', BOT: 'R', WEALTH: 'W', CASH: 'M' }

export const KEY_TO_ASSET_TYPE = { F: 'FUND', C: 'CRYPTO', R: 'BOT', W: 'WEALTH', M: 'CASH' }

// 券商佣金率（默认万 2.5; 在 config.py 改为你自己的费率）。影响"按股买"模式的手续费自动估算.
export const BROKER_COMMISSION_RATE = 0.00025

export const BROKER_COMMISSION_MIN = 5

export const STOCK_MARKETS = {
  A: { label: 'A股', placeholder: '600362', hint: '6位代码', minShares: 100, step: 100 },
  HK: { label: '港股', placeholder: '00700', hint: '港股5位代码', minShares: 1, step: 1 },
  US: { label: '美股', placeholder: 'AAPL', hint: '美股Ticker', minShares: 1, step: 1 },
}

// Fund subcategory: derived from name keywords. 顺序即匹配优先级 —— 具体主题在前,
// 宽基在后(否则 "创业板人工智能ETF" 会被 "创业板" 抢成宽基)。
export const FUND_CATEGORIES = [
  { id: 'gold',     label: '黄金',     match: /黄金|金ETF/ },
  { id: 'silver',   label: '白银',     match: /白银|银ETF/ },
  { id: 'bond',     label: '债券',     match: /债券|利率债|国债|信用债|可转债|纯债/ },
  { id: 'cash',     label: '货币',     match: /货币|活期|现金/ },
  { id: 'overseas', label: '海外股票', match: /QDII|纳斯达克|纳指|标普|道琼斯|美股|港股|恒生|中概|海外|日经|德国|法国|全球|港美/ },
  { id: 'semi',     label: '半导体',   match: /半导体|芯片|集成电路|存储/ },
  { id: 'ai',       label: '人工智能', match: /人工智能|智能|算力|机器人|AI/ },
  { id: 'tmt',      label: '科技TMT',  match: /通信|信息|数字经济|科技|计算机|软件|传媒|游戏|电子|互联|云计算|大数据/ },
  { id: 'newenergy',label: '新能源',   match: /新能源|光伏|锂电|储能|电池|风电|绿电|高端制造/ },
  { id: 'military', label: '军工',     match: /军工|国防|航天|航空/ },
  { id: 'medical',  label: '医药',     match: /医药|生物|医疗|疫苗|创新药|医疗器械|CXO/ },
  { id: 'consumer', label: '消费',     match: /消费|食品|白酒|酒|家电|零售|养殖|农业|旅游/ },
  { id: 'finance',  label: '金融',     match: /证券|券商|银行|保险|金融|地产/ },
  { id: 'commodity',label: '大宗商品', match: /原油|能源|煤炭|有色|铜|铁矿|豆粕|商品/ },
  { id: 'aindex',   label: 'A股宽基',  match: /沪深300|中证500|中证1000|中证2000|上证50|创业板|科创50|科创100|科创板|双创|A股|中证|国证|MSCI|价值|红利|低波/ },
]

// A-share industry: keyword-driven. 优先匹配后端拉的真实行业字符串
// (e.g. "电气设备-电源设备")，否则回退到股票名兜底。
export const A_SECTORS = [
  { id: 'metals',    label: '有色金属', match: /有色|铜|铝|锌|镍|铅|稀土|钼|黄金|白银|钢铁/ },
  { id: 'newenergy', label: '电气新能源', match: /电气设备|电源设备|储能|光伏|锂电|动力电池|电池|风电设备|新能源(?!车)/ },
  { id: 'energy',    label: '能源',     match: /石油|煤|燃气|电力|核电|风电|公用事业|采掘/ },
  { id: 'finance',   label: '金融',     match: /银行|证券|保险|期货|信托|非银金融/ },
  { id: 'realestate',label: '地产',     match: /地产|置业|建设|建筑/ },
  { id: 'tech',      label: '科技',     match: /科技|半导体|芯片|软件|信息|电子|通信|计算|传媒/ },
  { id: 'consumer',  label: '消费',     match: /消费|食品|酒|乳业|家电|家用电器|零售|百货|医美|纺织|休闲服务/ },
  { id: 'medical',   label: '医药',     match: /医药|生物|医疗|制药|疫苗/ },
  { id: 'auto',      label: '汽车',     match: /汽车|新能源车|整车/ },
  { id: 'materials', label: '材料',     match: /钢|水泥|玻璃|化工|塑料|纤维|建筑材料|建筑装饰|轻工制造/ },
  { id: 'machinery', label: '机械',     match: /机械|装备|工程|重工|军工|国防/ },
]

export const FAMILY_LABEL = {
  silver: '白银', gold: '黄金', metals: '有色金属',
  overseas: '海外股票', cn_broad: 'A股宽基',
}

// 同源家族索引: 以后端穿透出来的**行业**为准。
// 为什么不能再靠名字: 「兴业银锡」是银锡矿(东财行业 贵金属), 跟「山东黄金」本来就是同一块
// 风险, 但名字里一个金属字都没有 —— 下面那套关键词认不出它, 于是行内只有山东黄金挂了
// ↔同源, 银锡干干净净, 看着像是两注不相干的仓。穿不动明细的基金(黄金ETF联接这种)后端
// 已按名字兜底挂了行业, 这里一并算进来。
// 只有 ≥2 行一起扛着才算"同源"; 一行独占那是集中度, 另有规则管。
export const MIN_HOLDER_PCT = 0.5     // 一行在这个家族里不到总资产 0.5% 就不标了: 债基前十大里

// ============================================================
// Add stock form — compact, inline
// ============================================================
// 漂移三档的配色: 跟股价走是唯一需要刺眼的一档 —— 它说的是"你改的是说法, 不是事实"
export const DRIFT_TONE = {
  '跟股价走': 'text-bear-bright border-bear-border bg-bear-bg',
  '跟事实走': 'text-text border-border bg-surface-3',
  '只改了说法': 'text-warn border-border-med bg-surface-3',
  '判不了': 'text-text-muted border-border bg-surface-3',
}

// 去劣筛选: 只有"被排除/未被排除/判不了"三种结论。判不了刻意用灰而不是绿 ——
// 缺数据不是通过, 混成一个颜色就等于用缺失冒充合格。
export const SCREEN_TONE = {
  '通过': 'text-bull',
  '排除': 'text-bear-bright',
  '豁免': 'text-warn',
  '判不了': 'text-text-muted',
  '不适用': 'text-text-muted',
}
