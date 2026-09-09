# PDFMathTranslate Web (Next-Gen)

现代化、全解耦的 **PDFMathTranslate** 学术论文与文档翻译系统。基于 **React 19**、**Vite**、**Tailwind CSS v4** 与 **shadcn/ui** 构建，底层无缝整合由 **BabelDOC** 驱动的 `PDFMathTranslate-next` 2.0+ 核心排版引擎。

完整保留论文数学公式、双栏版面、图表、字体与排版样式，提供双语对照与全屏沉浸式文献阅读体验。

---

## 🌟 核心亮点与特性

### 1. 现代化交互与阅读体验
- **极简服务切换**：采用 shadcn/ui 紧凑下拉组件替代笨重大网格，未配 Key 自动提示一键去设置。
- **独立系统设置中心 (Settings Center)**：
  - **服务与密钥**：支持 SiliconFlow (官方免费/独立Key)、DeepSeek、OpenAI、Ollama 等平台，凭据仅保存在本地浏览器，刷新不丢失，不向服务端泄露密钥；
  - **高级排版输出**：支持**无水印渲染 (No Watermark)**、双语优先排序、交替对开阅读；
  - **学术术语提取**：支持自动术语提取开关、并发线程池与 QPS 调优；
- **沉浸式网页全屏阅读器**：右侧原生 PDF 预览支持一键**网页内部全屏**（`Esc` 退出），支持在「双语对照」、「单语译文」、「原始文档」三模无缝切换。
- **页码范围按需试跑 (Page Range)**：50+ 页大文档可指定如 `1-3` 或 `1,2,5` 秒级试跑出稿。

### 2. 用户鉴权与访问安全 (Authentication)
- **兼容上游 `auth.txt` 规范**：支持通过 `auth.txt` 文件或环境变量 `AUTH_USERS` 灵活配置多用户；
- **算力硬核拦截**：FastAPI 后端针对文件上传、大模型翻译调用进行强制 Token 校验，未授权请求直接返回 `401 Unauthorized`，彻底杜绝外人蹭用服务器算力；
- **开箱即用向下兼容**：未配置任何认证凭据时自动维持纯免密开放模式，本地调试零门槛。

### 3. 解耦与自动化 CI/CD
- **防腐层设计**：通过 Git Submodule 引入官方核心算法，上游更新与前端界面互不污染；
- **全自动构建与同步**：
  - `.github/workflows/docker-publish.yml`：代码推送到 `main` 自动构建一体化 Docker 镜像并发布到 GHCR；
  - `.github/workflows/sync-upstream.yml`：每日凌晨自动检测上游内核最新提交并运行冒烟测试。

---

## 🔐 用户登录鉴权配置

如需在公网部署时防止未授权访问，可任选以下一种方式开启鉴权：

### 方式 A：使用配置文件（完全兼容上游规范）
参考根目录的 [`auth.txt.example`](./auth.txt.example)，在 `data/auth.txt` 中写入账号密码（每行一个，支持逗号或冒号分隔）：
```text
admin,admin123456
user1,password123
```

### 方式 B：使用 Docker 环境变量（推荐）
在 `docker-compose.yml` 或启动命令中设置 `AUTH_USERS`：
```bash
AUTH_USERS="admin:admin123456,user1:password123"
```

> **提示**：若未配置 `auth.txt` 且未提供 `AUTH_USERS`，系统自动运行在**免登录开放模式**。

---

## 🐳 生产部署指南

### 方案 1：Docker Compose 一键启动（最推荐）

项目中已内置配置完善的 [`docker-compose.yml`](./docker-compose.yml)，自动处理端口映射、数据持久化与字体缓存：

```bash
# 1. 克隆代码仓库
git clone --recurse-submodules https://github.com/svefnz/PDFMathTranslate-web.git
cd PDFMathTranslate-web

# 2. 一键启动
docker compose up -d
```

启动后即可在浏览器访问 `http://你的服务器IP:8000`。

> 💡 **持久化说明**：
> - `./data` 目录挂载到宿主机，保存上传的原始文件与翻译输出成果；
> - `babeldoc-cache` 命名卷持久化存储排版中文字体（~130MB）与模型缓存，容器升级重建无需重新下载。

---

### 方案 2：直接拉取 GitHub 预构建镜像运行

```bash
docker run -d \
  --name pdfmathtranslate-web \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  --restart unless-stopped \
  ghcr.io/svefnz/pdfmathtranslate-web:latest
```

---

## 🌐 域名解析与反向代理 (Nginx / Caddy)

当通过子域名（如 `pdf.yourdomain.com`）反向代理本服务时，**务必在反向代理配置中添加以下两项关键设置**：

### Nginx 配置示例

```nginx
server {
    listen 80;
    server_name pdf.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name pdf.yourdomain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    # 1. 放宽上传体积限制（避免大型学术书籍或高清扫描件上传报 413）
    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 2. 关闭流式缓冲（确保 SSE 进度条和翻译事件实时推送到前端）
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

### Caddy 配置示例

```caddy
pdf.yourdomain.com {
    request_body {
        max_size 100MB
    }
    reverse_proxy 127.0.0.1:8000 {
        # Caddy 默认对 text/event-stream 禁用缓冲，超时时间可根据需要设置
        transport http {
            response_header_timeout 600s
        }
    }
}
```

---

## 💻 本地开发指南

### 1. 环境准备
- Node.js 18+ 或 [Bun](https://bun.sh/)
- Python 3.10 ~ 3.12

### 2. 一键启动本地开发环境
项目提供了自动分配空闲随机端口的开发脚本：
```bash
git clone --recurse-submodules https://github.com/svefnz/PDFMathTranslate-web.git
cd PDFMathTranslate-web
chmod +x dev.sh
./dev.sh
```

### 3. 构建前端产物
```bash
bun install
bun run build
```

---

## 📁 项目目录结构

```text
PDFMathTranslate-web/
├── upstream-core/                # [Git Submodule] 指向上游 PDFMathTranslate-next
├── backend/                      # 极简 FastAPI 适配层
│   ├── app.py                    # 接口路由 (鉴权, 上传, SSE 流式翻译, 文件服务)
│   ├── adapter.py                # 防腐层 (对接 upstream-core/pdf2zh_next)
│   └── requirements.txt          # 后端 Python 依赖
├── src/                          # React 19 前端源码
│   ├── components/               # 组件库 (LoginDialog, SettingsDialog)
│   │   └── ui/                   # shadcn/ui 原型库 (Select, Dialog, Tabs 等)
│   ├── types/                    # 本地设置与 TypeScript 接口定义
│   ├── App.tsx                   # 主界面 (工作区 + 全屏 PDF 对照阅读器)
│   └── main.tsx
├── .github/workflows/
│   ├── docker-publish.yml        # 代码推送自动构建并发布 Docker 镜像到 GHCR
│   └── sync-upstream.yml         # 每日定时自动检测上游更新并测试同步
├── Dockerfile                    # 多阶段生产镜像构建 (Bun 前端编译 + Python 后端一体化)
├── docker-compose.yml            # 生产环境一键容器编排
├── auth.txt.example              # 用户认证凭据配置参考模板
└── dev.sh                        # 本地动态随机端口开发启动脚本
```

## 👨‍💻 作者与维护者

- **维护者 (Maintainer)**: **朝代尾 ([@svefnz](https://github.com/svefnz))**
- **开源主页**: [https://github.com/svefnz/PDFMathTranslate-web](https://github.com/svefnz/PDFMathTranslate-web)

---

## 📄 开源许可证

本项目遵循 [AGPL-3.0 License](./upstream-core/LICENSE) 开源协议。核心算法由 [PDFMathTranslate-next](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next) 与 [BabelDOC](https://github.com/funstory-ai/BabelDOC) 提供。
