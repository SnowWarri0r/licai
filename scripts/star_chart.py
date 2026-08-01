#!/usr/bin/env python3
"""自渲染 star 历史曲线 SVG。

GitHub 2026-07 起把 stargazers 时间线端点收紧为仓库 admin/协作者的个人凭证
可读(Actions 安装令牌 403, 匿名 401), 曾用 fine-grained PAT 兜, 但 PAT 必然
到期(最短7天)→ 定时任务周期性挂掉。

现方案不需要任何 token: 累计曲线数据点存在仓库里(docs/star-history.json),
历史段已由时间线一次性回填; 每次运行只匿名读 repos/{repo} 的 stargazers_count
(该字段匿名可读), 数值变化时追加一个点。数据文件随 workflow 一起 commit。

用法: python3 scripts/star_chart.py [docs/star-history.svg]
      python3 scripts/star_chart.py --backfill   # 需 GITHUB_TOKEN, 重建历史段
纯标准库, 无第三方依赖。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

REPO = os.environ.get("GITHUB_REPOSITORY", "SnowWarri0r/licai")
DATA = os.environ.get("STAR_DATA", "docs/star-history.json")

W, H = 760, 400
ML, MR, MT, MB = 56, 28, 52, 44  # margins
ACCENT = "#c8a876"
GRID = "#e8e4dc"
TEXT = "#57606a"


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_points() -> list[tuple[datetime, int]]:
    """读仓库内的累计数据点 [(UTC时间, 累计star数)], 按时间升序。文件缺失/损坏返回空。"""
    try:
        with open(DATA) as f:
            raw = json.load(f).get("points") or []
    except (OSError, ValueError):
        return []
    out = []
    for p in raw:
        try:
            out.append((_parse(p[0]), int(p[1])))
        except (ValueError, TypeError, IndexError):
            continue
    return sorted(out)


def save_points(points: list[tuple[datetime, int]]) -> None:
    os.makedirs(os.path.dirname(DATA) or ".", exist_ok=True)
    with open(DATA, "w") as f:
        json.dump({
            "repo": REPO,
            "note": "star 累计曲线数据点 [UTC时间, 累计star数]。历史段由 stargazers 时间线一次性回填; "
                    "之后由 star-chart workflow 每天匿名读 stargazers_count 追加(仅在数值变化时), 无需任何 token。",
            "points": [[_iso(t), c] for t, c in points],
        }, f, ensure_ascii=False, indent=1)
        f.write("\n")


def _fetch_count_once(token: str) -> int:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "licai-star-chart"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return int(json.load(urllib.request.urlopen(req, timeout=30))["stargazers_count"])


def fetch_count() -> int:
    """当前 star 总数。repos/{repo} 的 stargazers_count 匿名可读, 不需要 token。
    环境里若有 token 先带上(限流额度高), 但过期/无效的 token 会让请求 401,
    所以带 token 失败一律退回匿名重试——凭证问题不该拖垮这条本就免鉴权的路径。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        try:
            return _fetch_count_once(token)
        except Exception as e:
            print(f"带 token 请求失败({e}), 退回匿名", file=sys.stderr)
    return _fetch_count_once("")


def backfill() -> list[tuple[datetime, int]]:
    """用 stargazers 时间线重建历史段(需 owner 个人凭证的 GITHUB_TOKEN)。仅手动跑。"""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.star+json",
               "User-Agent": "licai-star-chart"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    dates, page = [], 1
    while True:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/stargazers?per_page=100&page={page}",
            headers=headers)
        rows = json.load(urllib.request.urlopen(req, timeout=30))
        if not rows:
            break
        dates += [_parse(r["starred_at"]) for r in rows]
        if len(rows) < 100:
            break
        page += 1
    return [(t, i + 1) for i, t in enumerate(sorted(dates))]


def nice_step(n: int) -> int:
    for s in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if n / s <= 6:
            return s
    return max(1, n // 6)


def month_starts(t0: datetime, t1: datetime) -> list[datetime]:
    out, y, m = [], t0.year, t0.month
    while (y, m) <= (t1.year, t1.month):
        d = datetime(y, m, 1, tzinfo=timezone.utc)
        if d >= t0:
            out.append(d)
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def render(points: list[tuple[datetime, int]]) -> str:
    now = datetime.now(timezone.utc)
    n = points[-1][1]
    t0, t1 = points[0][0], now
    span = max((t1 - t0).total_seconds(), 1)
    ymax = max(n + max(1, n // 8), 5)
    px = lambda t: ML + (W - ML - MR) * (t - t0).total_seconds() / span
    py = lambda c: H - MB - (H - MB - MT) * c / ymax

    e = []
    e.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">')
    e.append(f'<rect width="{W}" height="{H}" rx="8" fill="#ffffff" stroke="{GRID}"/>')
    e.append(f'<text x="{ML}" y="30" font-size="16" font-weight="600" fill="#24292f">'
             f'Star History · {REPO}</text>')
    e.append(f'<text x="{W - MR}" y="30" font-size="11" fill="{TEXT}" text-anchor="end">'
             f'updated {now.strftime("%Y-%m-%d")} UTC</text>')

    step = nice_step(ymax)
    for c in range(0, ymax + 1, step):
        y = py(c)
        e.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" stroke="{GRID}"/>')
        e.append(f'<text x="{ML - 8}" y="{y + 4:.1f}" font-size="11" fill="{TEXT}" '
                 f'text-anchor="end">{c}</text>')
    for d in month_starts(t0, t1):
        x = px(d)
        e.append(f'<line x1="{x:.1f}" y1="{MT}" x2="{x:.1f}" y2="{H - MB}" stroke="{GRID}"/>')
        e.append(f'<text x="{x:.1f}" y="{H - MB + 18}" font-size="11" fill="{TEXT}" '
                 f'text-anchor="middle">{d.strftime("%Y-%m")}</text>')

    # 累计阶梯线: 每个数据点处计数跳到该点的累计值, 末端延伸到当前时刻
    pts = [f"M {px(points[0][0]):.1f} {py(points[0][1]):.1f}"]
    for t, c in points[1:]:
        pts.append(f"H {px(t):.1f} V {py(c):.1f}")
    pts.append(f"H {px(t1):.1f}")
    e.append(f'<path d="{" ".join(pts)}" fill="none" stroke="{ACCENT}" '
             f'stroke-width="2.5" stroke-linejoin="round"/>')
    ex, ey = px(t1), py(n)
    e.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4" fill="{ACCENT}"/>')
    # 终点计数标签放在端点上方空白处, 避免压线
    e.append(f'<text x="{ex - 8:.1f}" y="{ey - 10:.1f}" font-size="13" font-weight="600" '
             f'fill="#24292f" text-anchor="end">{n} stars</text>')
    e.append('</svg>')
    return "\n".join(e)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    out = args[0] if args else "docs/star-history.svg"

    if "--backfill" in sys.argv:
        points = backfill()
        if not points:
            print("backfill 拿不到时间线, 数据文件保持不动", file=sys.stderr)
            return 1
        save_points(points)
        print(f"{DATA}: 回填 {len(points)} 个点")
    else:
        points = load_points()
        count = fetch_count()
        # 只在总数变化时追加点, 避免每日无变化也写文件产生空 commit
        if not points or points[-1][1] != count:
            points.append((datetime.now(timezone.utc), count))
            save_points(points)
            print(f"{DATA}: {len(points)} 个点 (star 数 → {count})")
        else:
            print(f"{DATA}: star 数无变化 ({count}), 数据文件不动")

    if not points:
        print("无数据点, 保留现有图", file=sys.stderr)
        return 1
    svg = render(points)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        f.write(svg)
    print(f"{out}: {points[-1][1]} stars, {os.path.getsize(out)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
