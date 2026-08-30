#!/bin/bash
# 一键启动 licai (理财助手)。
# 首次运行自动: 建 venv + 装 Python 依赖 / 复制 config.py / npm install + 前端构建。
# 之后再跑, 只在依赖或前端源码变化时重做对应步骤, 否则直接起服务。
#
#   licai                    启动 (http://localhost:8888), 端口通了自动打开浏览器  [全局命令, 见下方安装]
#   ./start.sh               同上, 项目目录内直接跑也一样
#   licai --rebuild-frontend 强制重新构建前端 (忽略缓存判断)
#   licai --skip-frontend    跳过前端检查/构建 (只起后端, 用现有 static/)
#   licai --no-open          启动后不自动打开浏览器
#
# 装全局命令 (一次性): ln -sf "$(pwd)/start.sh" ~/.local/bin/licai
set -e

# $0 是 "licai" 这种符号链接命令名时不能直接 dirname; 顺着链接追到真实文件位置。
SRC="${BASH_SOURCE[0]}"
while [ -h "$SRC" ]; do
    LINK_DIR="$(cd -P "$(dirname "$SRC")" && pwd)"
    SRC="$(readlink "$SRC")"
    [[ "$SRC" != /* ]] && SRC="$LINK_DIR/$SRC"
done
DIR="$(cd -P "$(dirname "$SRC")" && pwd)"
cd "$DIR"

REBUILD_FRONTEND=0
SKIP_FRONTEND=0
OPEN_BROWSER=1
for arg in "$@"; do
    case "$arg" in
        --rebuild-frontend) REBUILD_FRONTEND=1 ;;
        --skip-frontend) SKIP_FRONTEND=1 ;;
        --no-open) OPEN_BROWSER=0 ;;
        -h|--help)
            sed -n '2,12p' "$SRC" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "未知参数: $arg (--rebuild-frontend / --skip-frontend / --no-open / --help)" >&2
            exit 1
            ;;
    esac
done

# ── 1. Python venv + 依赖 ──────────────────────────────
if [ ! -d venv ]; then
    echo "[start] 创建 venv ..."
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
STAMP="venv/.requirements.sha256"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$REQ_HASH" ]; then
    echo "[start] 安装 Python 依赖 ..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    echo "$REQ_HASH" > "$STAMP"
fi

# ── 2. config.py ───────────────────────────────────────
if [ ! -f config.py ]; then
    echo "[start] 未找到 config.py, 从模板复制 (记得按需修改佣金率等参数)"
    cp config.example.py config.py
fi

# ── 3. 前端 ────────────────────────────────────────────
if [ "$SKIP_FRONTEND" = "1" ]; then
    echo "[start] 跳过前端构建 (--skip-frontend)"
elif [ ! -f static/index.html ] || [ "$REBUILD_FRONTEND" = "1" ] || [ frontend/package-lock.json -nt frontend/node_modules ]; then
    if ! command -v node >/dev/null 2>&1; then
        echo "[start] 未找到 node, 请先安装 Node.js >= 20 (https://nodejs.org 或 brew install node)" >&2
        exit 1
    fi
    NODE_MAJOR="$(node -e 'console.log(process.versions.node.split(".")[0])')"
    if [ "$NODE_MAJOR" -lt 20 ]; then
        echo "[start] 当前 node $(node -v), 前端构建需要 >= 20, 请升级后重试 (brew install node@20)" >&2
        exit 1
    fi

    cd frontend
    if [ ! -d node_modules ]; then
        echo "[start] 安装前端依赖 (npm install) ..."
        npm install
    fi
    echo "[start] 构建前端 ..."
    npm run build
    cd "$DIR"
else
    echo "[start] 前端已构建, 跳过 (加 --rebuild-frontend 可强制重建)"
fi

# ── 4. 启动 ────────────────────────────────────────────
# exec 换掉当前 shell 进程好让 Ctrl+C/信号直达 python, 之后这个脚本就不会再往下走了;
# 所以浏览器要在 exec 之前、以后台子 shell 的形式挂上 —— 轮询端口通了(而不是傻等几秒)
# 才 open, 不跟 python 的启动抢前台, server 没起来也不会打开一个打不开的页面。
if [ "$OPEN_BROWSER" = "1" ] && command -v open >/dev/null 2>&1; then
    ( for _ in $(seq 1 60); do
        curl -sf -o /dev/null http://localhost:8888/ 2>/dev/null && { open "http://localhost:8888"; break; }
        sleep 0.5
      done ) &
fi

echo "[start] 启动服务 → http://localhost:8888"
exec python run.py
