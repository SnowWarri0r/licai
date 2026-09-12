"""算术兜底: 关键数字不许心算。

**为什么要它**: LLM 做乘除和百分比时会错, 而错得"看起来很合理" —— 市值 = 价 × 股本 差一个
数量级、涨幅算成 (新-旧)/新、同比与环比混用, 这类错混在一段通顺的分析里几乎抓不出来。
ai-berkshire 的做法是硬规则「禁止心算, 必须 Bash 调 financial_rigor.py」, 这里搬同一条约束:
给 agent 一个算术工具, 并在系统提示里要求关键数字走它。

只做**能验证**的事, 不做估值判断:
  · 四则/百分比/比例的精确计算(Decimal, 不吃浮点误差)
  · 市值验算: 价 × 股本 与外部报出的市值对不对得上, 差多少
  · 交叉验证: 同一字段的多个来源值是否一致, 报离散度
不做的: 内在价值、目标价、三情景推演 —— 那些是估值结论, 本项目不给。
"""
from __future__ import annotations
import ast as _ast
from decimal import Decimal, InvalidOperation, getcontext

getcontext().prec = 28

def _d(x) -> Decimal:
    return Decimal(str(x))


_BINOPS = {_ast.Add: lambda a, b: a + b, _ast.Sub: lambda a, b: a - b,
           _ast.Mult: lambda a, b: a * b, _ast.Div: lambda a, b: a / b}


def _walk(node) -> Decimal:
    """只认: 数字字面量 / 一元正负 / 四则二元运算。别的一律拒。

    **不用 eval**: 字符白名单挡不住幂运算炸弹 —— 白名单里有 `*`, 于是 `9**9**9` 写得出来,
    足够把进程挂死。所以走 AST 白名单, 顺便也就没有代码执行面了(Pow 直接不在表里)。
    数字在这里才转 Decimal, 而不是让 Python 先解析成 float —— 只调 getcontext().prec 是
    没用的, 实测 0.1+0.2 那样会得到 0.30000000000000004。
    """
    if isinstance(node, _ast.Expression):
        return _walk(node.body)
    if isinstance(node, _ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("只支持数字")
        return Decimal(repr(node.value)) if isinstance(node.value, float) else Decimal(node.value)
    if isinstance(node, _ast.UnaryOp) and isinstance(node.op, (_ast.UAdd, _ast.USub)):
        v = _walk(node.operand)
        return v if isinstance(node.op, _ast.UAdd) else -v
    if isinstance(node, _ast.BinOp):
        fn = _BINOPS.get(type(node.op))
        if not fn:
            raise ValueError(f"不支持的运算 {type(node.op).__name__}(只支持 + - * /)")
        return fn(_walk(node.left), _walk(node.right))
    raise ValueError(f"不支持的写法 {type(node).__name__}")


def evaluate(expr: str) -> dict:
    """算一个算术表达式。只放行数字与四则运算 —— 走 AST 白名单, 不是 eval。"""
    e = (expr or "").strip().replace("×", "*").replace("÷", "/").replace("，", "")
    if not e:
        return {"error": "空表达式"}
    if "%" in e:                      # 5% → (5/100), 只支持紧跟在数字后面
        e = e.replace("%", "/100")
    try:
        out = _walk(_ast.parse(e, mode="eval"))
    except (SyntaxError, ZeroDivisionError, InvalidOperation, ValueError, TypeError) as ex:
        return {"error": f"算不了: {ex}"}
    return {"表达式": expr, "结果": float(out), "精确值": str(out.normalize())}


def verify_market_cap(price, shares, reported=None, unit: str = "亿") -> dict:
    """市值验算: 价 × 股本。shares 按 unit 计(默认亿股), reported 是外部报出的市值(同 unit)。"""
    try:
        p, sh = _d(price), _d(shares)
    except InvalidOperation:
        return {"error": "价格或股本不是数字"}
    calc = p * sh
    out = {"算出市值": f"{float(calc):.4f}{unit}", "口径": f"{price} × {shares}{unit}股"}
    if reported is not None:
        try:
            rep = _d(reported)
        except InvalidOperation:
            return {**out, "error": "reported 不是数字"}
        if rep == 0:
            return {**out, "error": "reported 为 0, 无法比"}
        diff = (calc - rep) / rep * 100
        out.update({"外部报出": f"{float(rep):.4f}{unit}",
                    "偏差%": round(float(diff), 3),
                    "判定": _cap_verdict(calc, rep, diff)})
    return out


def _cap_verdict(calc: Decimal, rep: Decimal, diff: float) -> str:
    """量级错要两个方向都认出来。

    只用 abs(偏差%)>900 判"差数量级"是错的: 报出值比算出值大 100 倍时偏差是 -99%,
    落不进那个区间(实测 4.36×21.9=95.48 对 reported=9550, 判成了"需查口径")。
    所以看**比值**而不是偏差百分比。
    """
    if abs(diff) <= 1:
        return "一致"
    ratio = (calc / rep) if rep else Decimal(0)
    for k in (Decimal(10), Decimal(100), Decimal(1000), Decimal(10000)):
        for r in (ratio, 1 / ratio if ratio else Decimal(0)):
            if r and abs(r - k) / k <= Decimal("0.02"):
                return f"正好差 {int(k)} 倍, 大概率单位搞错了(亿/万/元, 或总股本与流通股本)"
    return "对不上, 需查口径(总股本 vs 流通股本 / 币种 / 复权)"


def cross_validate(field: str, values: list, tol_pct: float = 1.0) -> dict:
    """同一字段多来源交叉验证: 一致就用, 不一致把离散度报出来让调用方摆两个数。"""
    nums = []
    for v in values or []:
        try:
            nums.append(_d(v))
        except InvalidOperation:
            continue
    if len(nums) < 2:
        return {"字段": field, "error": "至少要两个来源的值才能交叉验证"}
    lo, hi = min(nums), max(nums)
    base = abs(lo) if lo != 0 else abs(hi)
    spread = float((hi - lo) / base * 100) if base else 0.0
    return {"字段": field, "各来源": [float(n) for n in nums],
            "最小": float(lo), "最大": float(hi), "离散%": round(spread, 3),
            "一致": spread <= tol_pct,
            "判定": ("两源一致, 可直接引用" if spread <= tol_pct
                     else f"两源相差 {spread:.2f}%(>{tol_pct}%), 按项目口径列出两个数并指明存疑")}
