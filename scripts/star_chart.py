#!/usr/bin/env python3
"""自渲染 star 历史曲线 SVG。

GitHub 2026-07 起把 stargazers 端点收紧为仓库 admin/协作者可读,
star-history.com 的无 token 嵌图对本仓库失效, 改为用仓库自带
GITHUB_TOKEN 定时拉时间线自己画(见 .github/workflows/star-chart.yml)。

用法: GITHUB_TOKEN=xxx python3 scripts/star_chart.py docs/star-history.svg
纯标准库, 无第三方依赖。
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

REPO = os.environ.get("GITHUB_REPOSITORY", "SnowWarri0r/licai")

W, H = 760, 400
ML, MR, MT, MB = 56, 28, 52, 44  # margins
ACCENT = "#c8a876"
GRID = "#e8e4dc"
TEXT = "#57606a"


def fetch_star_dates() -> list[datetime]:
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
        dates += [datetime.fromisoformat(r["starred_at"].replace("Z", "+00:00"))
                  for r in rows]
        if len(rows) < 100:
            break
        page += 1
    return sorted(dates)


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


def render(dates: list[datetime]) -> str:
    now = datetime.now(timezone.utc)
    n = len(dates)
    t0, t1 = dates[0], now
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

    # 累计阶梯线: 第 i 颗 star 处计数跳到 i+1, 末端延伸到当前时刻
    pts = [f"M {px(dates[0]):.1f} {py(1):.1f}"]
    for i, d in enumerate(dates[1:], start=2):
        x = px(d)
        pts.append(f"H {x:.1f} V {py(i):.1f}")
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
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/star-history.svg"
    dates = fetch_star_dates()
    if not dates:
        print("no stargazer data, keep existing chart", file=sys.stderr)
        return
    svg = render(dates)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w") as f:
        f.write(svg)
    print(f"{out}: {len(dates)} stars, {os.path.getsize(out)} bytes")


if __name__ == "__main__":
    main()
