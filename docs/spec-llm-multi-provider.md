# Spec: 多厂商 Anthropic-协议兼容 LLM 客户端

**日期**: 2026-06-20
**状态**: 待实现
**分支**: main (fork)

---

## 1. 背景与动机

当前 `services/llm_client.py` 写死了 Anthropic 官方 API 地址 `api.anthropic.com`，只支持两种鉴权方式：

- `ANTHROPIC_API_KEY` 环境变量（API key 模式）
- macOS Keychain OAuth token（Claude Code CLI 登录后写入）

**问题**：
1. 官方 API 需要代理访问，国内网络不稳定
2. 无法使用国内模型厂商（如 DeepSeek、Moonshot、百川、通义千问等）提供的 Anthropic-协议兼容端点
3. 模型名写死在各个调用方（`claude-haiku-4-5-20251001` 等），切换厂商需要改代码

**目标**：让项目支持任意遵循 Anthropic Messages API 协议的 API 端点，同时保持向后兼容。

---

## 2. 什么是"Anthropic-协议兼容"

国内多个模型厂商对外暴露的 API 端点遵循 Anthropic Messages API 格式：

```
POST /v1/messages
Headers:
  Content-Type: application/json
  x-api-key: <key>           # Anthropic 官方用这个 header
  anthropic-version: 2023-06-01

Body:
{
  "model": "<model-id>",
  "max_tokens": 2048,
  "system": "system prompt string or array of content blocks",
  "messages": [{"role": "user", "content": "..."}]
}

Response:
{
  "content": [{"type": "text", "text": "..."}],
  "stop_reason": "end_turn",
  ...
}
```

只要厂商端点遵循这个请求/响应格式，就可以无缝接入。已知兼容厂商包括：

| 厂商 | Base URL 示例 | API Key Header |
|------|-------------|----------------|
| Anthropic 官方 | `https://api.anthropic.com` | `x-api-key` |
| DeepSeek | `https://api.deepseek.com` | `Authorization: Bearer` |
| 硅基流动 (SiliconFlow) | `https://api.siliconflow.cn` | `Authorization: Bearer` |
| OpenRouter | `https://openrouter.ai/api` | `Authorization: Bearer` |
| 自定义代理 | 任意 | 可配置 |

---

## 3. 设计方案

### 3.1 配置层

新增配置项，优先级：**环境变量 > DB 配置 > 默认值**

| 配置项 | 环境变量 | DB key | 默认值 | 说明 |
|--------|---------|--------|--------|------|
| API Base URL | `LLM_BASE_URL` | `llm_base_url` | `https://api.anthropic.com` | 不含 `/v1/messages` |
| API Key | `LLM_API_KEY` | `llm_api_key` | 无 | 通用 API key，兼容所有厂商 |
| API Key Header | `LLM_API_KEY_HEADER` | `llm_api_key_header` | `x-api-key` | 鉴权 header 名，如 `Authorization: Bearer` 则填 `Authorization` |
| API Key Prefix | `LLM_API_KEY_PREFIX` | `llm_api_key_prefix` | 无 | 鉴权值前缀，如填 `Bearer` 则 header 为 `Authorization: Bearer <key>` |
| 代理地址 | `LLM_PROXY` | `llm_proxy` | 无 | HTTP 代理，国内厂商可留空直连 |

**向后兼容**：
- 如果 `LLM_API_KEY` 没设置，继续走 `ANTHROPIC_API_KEY` → Keychain OAuth 的鉴权链
- 如果 `LLM_BASE_URL` 没设置，默认用 `https://api.anthropic.com`
- 这意味着不配置任何新东西 = 行为完全不变

### 3.2 模型名映射

当前各调用方散布着硬编码模型名。引入**模型别名映射**，让调用方可以用逻辑名。

| 逻辑名 | 默认模型 | 说明 |
|--------|---------|------|
| `smart` | `claude-opus-4-8-20250514` | 复杂推理（问问市场、解套分析） |
| `balanced` | `claude-sonnet-4-6-20250514` | 中等任务（新闻解读、板块分析） |
| `fast` | `claude-haiku-4-5-20251001` | 轻量任务（基本面评分、晨报） |

映射可通过 `LLM_MODEL_MAP` 环境变量或 DB `llm_model_map` 配置为 JSON：
```json
{"smart": "deepseek-chat", "balanced": "deepseek-chat", "fast": "deepseek-chat"}
```

**向后兼容**：直接传真实模型名（如 `claude-sonnet-4-6-20250514`）仍然有效，不做映射转换。

### 3.3 API 端点构建

```
base_url + "/v1/messages" + ("?beta=true" if base_url contains "anthropic.com" else "")
```

`?beta=true` 是 Anthropic 特有的，国内厂商不需要。

### 3.4 鉴权 header 构建

```python
if api_key_header == "Authorization":
    headers["Authorization"] = f"{api_key_prefix} {api_key}".strip()
else:
    headers[api_key_header] = api_key
```

默认行为（不配置新项时）：`x-api-key: <key>`，与 Anthropic 官方一致。

### 3.5 OAuth / Claude Code 兼容

当 `LLM_API_KEY` 有值时：走通用 API key 模式，不使用 OAuth header。
当 `LLM_API_KEY` 无值时：走原有 `ANTHROPIC_API_KEY` → Keychain OAuth 链，保留 `CLAUDE_CODE_HEADERS`。

---

## 4. 涉及文件

| 文件 | 改动 |
|------|------|
| `services/llm_client.py` | **核心改动**：重构为可配置的多厂商客户端 |
| `api/settings_routes.py` | 新增 LLM 配置读写 API |
| `database.py` | 新增 `llm_config` 相关读写函数 |
| `run.py` | 启动时从 DB 加载 LLM 配置并 apply |
| 各调用方 | 可选：将硬编码模型名替换为逻辑别名 |

---

## 5. API 设计

### 5.1 Settings API 新增端点

```
GET  /api/settings/llm          → 返回当前 LLM 配置（脱敏）
POST /api/settings/llm          → 保存 LLM 配置
POST /api/settings/llm/test     → 用当前配置发一条测试请求，返回成功/失败+延迟
```

### 5.2 前端 Settings 页面

在 Settings UI 中新增"LLM 配置"区域：
- API Base URL 输入框
- API Key 输入框（密码类型）
- API Key Header 输入框
- API Key Prefix 输入框
- 模型映射 JSON 编辑区
- "测试连接"按钮

---

## 6. 自测计划

### 6.1 单元测试

1. **配置解析测试**：验证 env var → DB → default 优先级
2. **鉴权 header 构建测试**：`x-api-key` 模式 vs `Authorization: Bearer` 模式
3. **模型名映射测试**：逻辑名 → 实际模型名、直传模型名不变
4. **URL 构建测试**：Anthropic 官方 vs 第三方端点
5. **向后兼容测试**：不配置新项时行为完全不变

### 6.2 集成测试

1. 用 DeepSeek API 发一条真实请求，验证响应解析正确
2. 切换回 Anthropic 官方，验证不影响现有功能

---

## 7. 实现步骤

1. **重构 `llm_client.py`**：抽配置、URL 构建、鉴权 header、模型映射
2. **新增 DB 读写函数**：`get_llm_config()` / `save_llm_config()`
3. **新增 Settings API 端点**
4. **修改 `run.py`**：启动时加载配置
5. **编写测试**
6. **Code review via subagent**
