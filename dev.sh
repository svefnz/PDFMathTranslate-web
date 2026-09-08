#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== 启动 PDFMathTranslate-web 本地开发环境 ==="

# 确保子模块代码存在
if [ ! -f "upstream-core/pdf2zh_next/__init__.py" ]; then
    echo "正在初始化 upstream-core 子模块..."
    git submodule update --init --recursive
fi

# 检查 Python 依赖
echo "正在检查并安装 Python 依赖..."
pip install -r backend/requirements.txt -q
pip install -e ./upstream-core -q

# 启动后端 (后台)
echo "正在启动 FastAPI 后端服务 (端口 8000)..."
python3 -m uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

cleanup() {
    echo "正在停止服务..."
    kill $BACKEND_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# 启动前端
echo "正在启动 Vite 前端开发服务器 (端口 5173)..."
bun run dev --host

