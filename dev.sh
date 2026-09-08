#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 启动 PDFMathTranslate-web 本地开发环境 ==="

# 1. 确保子模块代码存在
if [ ! -f "upstream-core/pdf2zh_next/__init__.py" ]; then
    echo "正在初始化 upstream-core 子模块..."
    git submodule update --init --recursive
fi

# 2. 检查并准备 Python 虚拟环境 (避免系统 Python / Homebrew PEP 668 限制)
if command -v python3 >/dev/null 2>&1; then
    BASE_PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    BASE_PYTHON="python"
else
    echo "❌ 错误: 未检测到 Python 环境，请先安装 Python 3.10+！"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "📦 正在创建 Python 虚拟环境 (.venv)..."
    if command -v uv >/dev/null 2>&1; then
        uv venv .venv
    else
        $BASE_PYTHON -m venv .venv
    fi
fi

# 激活虚拟环境
source .venv/bin/activate

# 3. 检查并安装 Python 依赖
echo "📦 正在检查并安装 Python 依赖..."
if command -v uv >/dev/null 2>&1; then
    uv pip install -r backend/requirements.txt -e ./upstream-core
else
    python -m pip install --upgrade pip -q
    python -m pip install -r backend/requirements.txt -e ./upstream-core
fi

# 4. 启动后端 (后台运行)
echo "🚀 正在启动 FastAPI 后端服务 (http://localhost:8765)..."
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8765 --reload &
BACKEND_PID=$!

cleanup() {
    echo ""
    echo "🛑 正在停止所有服务..."
    kill $BACKEND_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 5. 启动前端
echo "🎨 正在启动 Vite 前端开发服务器 (http://localhost:5173)..."
if command -v bun >/dev/null 2>&1; then
    bun run dev --host
elif command -v pnpm >/dev/null 2>&1; then
    pnpm run dev --host
elif command -v npm >/dev/null 2>&1; then
    npm run dev -- --host
else
    echo "❌ 错误: 未检测到 bun / pnpm / npm 前端工具，请安装 Node/Bun！"
    exit 1
fi
