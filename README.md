# PDFMathTranslate-web

现代化、全解耦的 **PDFMathTranslate** Web 前端界面与应用包装，基于 React 19、Vite 8、Tailwind CSS v4 与 shadcn/ui 构建，具备双栏对照、流式进度推送与多引擎切换支持。

---

## 🌟 核心特性

- **现代纯前端架构**：基于 React 19 + TypeScript + Vite 8 + Tailwind CSS 4 + shadcn/ui 构建。
- **解耦防腐设计**：通过 `upstream-core` Git Submodule 引入官方核心算法，上游迭代不影响前端业务。
- **SSE 流式实时更新**：原生 Server-Sent Events (SSE) 协议实时接收翻译阶段与分段百分比。
- **双栏交互与对照预览**：支持原始文件、双语对照版、单语译文版无缝切换与在线预览。
- **GitHub Actions 自动同步**：内置定时工作流，每天检测上游最新 Commit 并运行自动化冒烟测试。
- **极简部署**：支持单容器 Docker 部署，前端打包静态资源直接由后端统一托管。

---

## 📁 目录结构

```text
PDFMathTranslate-web/
├── upstream-core/                # [Git Submodule] 指向上游 PDFMathTranslate-next
├── backend/                      # 极简 FastAPI 适配层
│   ├── app.py                    # 接口路由 (上传, SSE 流式翻译, 文件服务)
│   ├── adapter.py                # 防腐层 (调用 upstream-core/pdf2zh_next)
│   └── requirements.txt          # 后端依赖
├── src/                          # React 前端源码
│   ├── components/ui/            # shadcn/ui 组件库
│   ├── App.tsx                   # 主界面 (工作区 + 预览)
│   └── main.tsx
├── .github/workflows/
│   └── sync-upstream.yml         # 每日定时自动同步上游 Submodule
├── .gitmodules                   # 子模块跟踪配置 (branch = main)
├── Dockerfile                    # 多阶段生产镜像构建
├── dev.sh                        # 本地一键开发启动脚本
└── vite.config.ts                # Vite 配置与 API 反向代理
```

---

## 🚀 快速启动

### 1. 克隆与子模块初始化

```bash
git clone --recurse-submodules <你的仓库地址>
cd PDFMathTranslate-web
```

### 2. 本地一键开发启动

```bash
./dev.sh
```

或者手动分别启动：

**启动后端：**
```bash
pip install -r backend/requirements.txt
pip install -e ./upstream-core
python3 -m uvicorn backend.app:app --reload --port 8000
```

**启动前端：**
```bash
bun install  # 或 npm install
bun run dev  # 访问 http://localhost:5173
```

---

## 🐳 Docker 生产部署

```bash
docker build -t pdf2zh-web .
docker run -d -p 8000:8000 --name pdf2zh-web-app pdf2zh-web
```
浏览器打开 `http://localhost:8000` 即可访问。
