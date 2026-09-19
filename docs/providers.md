# 扩展数据源（provider）

本项目自带的行情源——东财、新浪、腾讯、Yahoo、SEC、akshare——都是**公开接口**：不要账号、不要凭证、不伪装客户端。装了这个项目，开箱就是这些。

少数口径不在其中：带身份标签的深度龙虎榜席位、集合竞价阶段的板块异动、季报口径的机构行业增减仓、三周以上的涨停历史。这些只有商业终端才有，接它们意味着要带**你自己的账号凭证**去打对方的接口。

那是你自己与对方的关系，不该由这个仓库替所有装了它的人预置一条直连通道。所以：

> **本仓库只定义协议，不自带任何 provider 实现。** 什么都不配 = `NullProvider`，所有扩展能力返回"未接入"，其余功能一切照常。

要接哪个源，自己写一个包（或装一个别人写的），实现下面的协议，再把入口填进配置。装不装、配不配，是你自己按的那一次开关。

## 开与关

```bash
export MARKET_PROVIDER='mypkg.myprovider:Provider'   # 或在 设置 → 扩展数据源 里填
```

配置双通道，与 tdx / 知识星球同模式：环境变量 `MARKET_PROVIDER` 优先，回落数据库 `config` 表的 `market_provider` 键。留空即卸载。

### 让自己被列进候选

装好的包可以在 `licai.providers` 这个 entry point 组里登记，设置页就会把它列成「已装可选」，点一下填进去，不用手敲导入路径：

```toml
# 你的 provider 包的 pyproject.toml
[project.entry-points."licai.providers"]
kpl = "licai_provider_kpl:Provider"
```

登记之后 `MARKET_PROVIDER=kpl` 也能用（等价于写全路径）。

两点要说清楚：

- **列出来 ≠ 启用。** 装了包仍然要显式选一次，`pip install` 本身不会让任何数据源生效——这是这一层存在的理由，有专门的测试钉住。
- **列候选只读包元数据，不 import 任何 provider 模块。** 否则光是装上某个包，它的代码就会在每次打开设置页时被执行一遍，「没配就零 import」当场就破了。所以候选里只有名字、spec、包名和版本，没有 `display_name` 那种要实例化才拿得到的东西。

加载失败（拼错、模块不存在、不是 `MarketProvider` 的实现）一律退回 `NullProvider` 并把原因记在 `registry.load_error()`，设置页会显示——一个配错的扩展源不该把整个服务带崩。

## 协议

`services/providers/base.py` 是唯一的契约。实现一个 provider 就是：继承 `MarketProvider`，声明 `capabilities`，覆盖对应方法。

| 能力常量 | 方法 | 这一项补的是什么 |
|---|---|---|
| `CAP_SENTIMENT` | `sentiment(day)` | 东财只给涨停家数/炸板率之外的情绪维度 |
| `CAP_STOCK_LHB` | `stock_lhb(code, day)` | 龙虎榜席位的身份标签（东财只给裸营业部名） |
| `CAP_PLATE_BIDDING` | `plate_bidding()` | 集合竞价阶段的板块异动 |
| `CAP_HOT_THEMES` | `hot_themes()` | 月度题材热度 |
| `CAP_INST_POSITION` | `inst_position(date, top)` | 季报口径的机构行业增减仓 |
| `CAP_LIMIT_UP_HISTORY` | `limit_up_history(day)` | 涨停档案历史回填（东财只有滚动约 3 周） |
| `CAP_LIMIT_UP_THEMES` | `limit_up_themes(day)` | 涨停个股的题材标签（东财只给行业） |

只实现其中一两项完全可以——没声明的能力，对应的路由返回 `available: false`，对应的 agent 工具根本不会挂给模型。

### 三种失败必须分得开

```
CapabilityUnavailable  → {"available": false}      没接入 / 这个 provider 不提供这项
ProviderAuthError      → {"need_login": true}      凭证未配或已失效
ProviderError          → {"error": "..."}          这次没取到（网络 / 对方报错）
```

翻译由 `services/providers/gateway.py` 统一做，provider 只管抛对异常。这三者在界面上长得不一样：没接入该引导去装，凭证失效该引导去重填，取数失败该说稍后再试。压成同一个空状态，用户只能对着空界面猜是哪一种。

旁路调用（如情绪面的第二数据源）走 `gateway.try_call`：拿不到就整块省掉，不把提示混进主口径的返回里。

### 凭证

provider 用 `credential_fields` 声明自己要填哪些字段，设置页照着通用渲染——**不为任何一个 provider 写死表单**。标了 `secret` 的字段只存不回显；`echo` 的（如账号 ID）可以回显。

存取由 provider 自己的 `save_credentials` / `clear_credentials` 负责，键名归它自己管。凭证存本机 SQLite，不进代码库。

## 一个最小实现

```python
from services.providers.base import CAP_HOT_THEMES, MarketProvider

class Provider(MarketProvider):
    name = "demo"
    display_name = "示例源"
    capabilities = frozenset({CAP_HOT_THEMES})

    async def status(self):
        return {"provider": self.name, "display_name": self.display_name,
                "configured": True, "valid": True, "note": "无需凭证"}

    async def hot_themes(self):
        return {"有数据": True, "数量": 1,
                "题材榜": [{"排名": 1, "题材": "示例", "代码": "000000"}],
                "note": "客观数据，非买卖建议。"}
```

```bash
export MARKET_PROVIDER='mydemo:Provider'
```

## 给 provider 作者的三条约定

1. **返回结构原样透传到前端**，所以键名沿用各功能既有的中文结构（见上表各方法的文档串）。换 provider 只要产出同形状，上层一行都不用改。
2. **没有的字段留空，不猜。** 匹配不上的列宁可给 `None`——回填数据是要落库并被回测读的，错位就是长期污染。涨停档案另有"扩展源不许覆盖东财"的冲突规则（`database.save_limit_up_pool`）。
3. **客观数据，不给买卖建议。** 与本项目其余部分同一条红线。
