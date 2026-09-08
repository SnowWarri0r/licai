"""开盘啦登录态: 凭证读取 + 带 Token 的请求 + Token 失效检测。

登录态接口(竞价异动、深度龙虎榜游资标签等)要带 UserID+Token。Token 来自**用户自己账号**的
一次登录(长期有效, 无自动续签, 失效后重新登录获取), 用于用户自己的炒股助手。

凭证不硬编码、不进 git: 走项目既有的双通道(env 优先 → 回落 DB config, 与 tdx/zsxq 同模式)。
  env:  KPL_UID / KPL_TOKEN     (run.py 启动时从 .env 载入, .env 被 gitignore 挡着)
  DB :  get_config('kpl_uid') / get_config('kpl_token')  (设置页可改)

Token 会失效(改密码/长期未用/被踢)。失效时**明确抛 KplAuthError**, 让调用方提示"去重新登录"
而不是把空数据当成"今天没行情" —— 后者会让用户对着一个其实是登录过期的空界面纳闷。
"""
from __future__ import annotations

import asyncio
import os
import uuid

_VER = "5.21.0.2"
_UA = "Dalvik/2.1.0 (Linux; U; Android 9; Build/PQ3A.190605.01141736)"

# 无效/过期登录态的报错特征。errcode 非 "0" 且 errmsg 提到这些 = Token 该刷新了。
_AUTH_FAIL_MARKERS = ("token", "登录", "登陆", "未授权", "重新登录", "身份")


class KplAuthError(RuntimeError):
    """开盘啦登录态失效(Token 过期/无效/未配置)。调用方据此提示用户重新登录。"""


async def credentials() -> tuple[str, str] | None:
    """(UserID, Token) 或 None(没配)。env 优先, 回落 DB config。"""
    uid = os.environ.get("KPL_UID")
    tok = os.environ.get("KPL_TOKEN")
    if not (uid and tok):
        try:
            from database import get_config
            uid = uid or await get_config("kpl_uid")
            tok = tok or await get_config("kpl_token")
        except Exception:
            pass
    return (uid, tok) if (uid and tok) else None


def _looks_like_auth_fail(j) -> bool:
    if not isinstance(j, dict):
        return False
    if str(j.get("errcode", "0")) == "0":
        return False
    msg = str(j.get("errmsg") or "") + str(j.get("errcode") or "")
    return any(m in msg.lower() if m.isascii() else m in msg for m in _AUTH_FAIL_MARKERS)


def _post_sync(host: str, params: dict, uid: str, tok: str):
    import requests
    s = requests.Session()
    s.trust_env = False
    data = {"PhoneOSNew": "1", "DeviceID": str(uuid.uuid4()), "VerSion": _VER,
            "apiv": "w42", "UserID": uid, "Token": tok}
    data.update(params)
    try:
        r = s.post(f"https://{host}.longhuvip.com/w1/api/index.php", data=data,
                   headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                            "User-Agent": _UA, "Host": f"{host}.longhuvip.com"},
                   timeout=15)
        return r.json()
    except Exception:
        return None


async def call(host: str, params: dict):
    """带登录态打一个接口。

    - 没配凭证 → KplAuthError('未配置')
    - 服务端报登录失效 → KplAuthError('已过期')
    - 网络/解析失败 → None(调用方按"这次没取到"处理, 不是登录问题)
    - 成功 → 原始 json
    """
    cred = await credentials()
    if not cred:
        raise KplAuthError("未配置开盘啦登录态(设 KPL_UID/KPL_TOKEN 或在设置里填)")
    uid, tok = cred
    j = await asyncio.to_thread(_post_sync, host, params, uid, tok)
    if _looks_like_auth_fail(j):
        raise KplAuthError(f"开盘啦登录态已失效, 请重新登录抓取 Token(服务端: {j.get('errmsg')})")
    return j


async def check() -> dict:
    """探活: 用当前凭证调龙虎榜列表(盘后也稳定有数据), 报告 Token 还有效没。设置页/诊断用。"""
    cred = await credentials()
    if not cred:
        return {"configured": False, "valid": False, "note": "未配置 KPL_UID/KPL_TOKEN"}
    uid, _ = cred
    try:
        from datetime import datetime, timezone, timedelta
        day = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
        j = await call("applhb", {"a": "GetStockList", "c": "LongHuBang",
                                  "Type": "2", "Time": day, "Index": "0", "st": "20"})
        ok = isinstance(j, dict) and str(j.get("errcode", "0")) == "0"
        return {"configured": True, "valid": ok, "uid": uid,
                "note": "Token 有效" if ok else "Token 可能失效(接口未正常返回)"}
    except KplAuthError as e:
        return {"configured": True, "valid": False, "uid": uid, "note": str(e)}
