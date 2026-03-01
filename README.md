# 读写练习（Zeabur 上线版）

本项目目标是公网可访问、必须登录、可持续运行的前后端分离部署。

## 目录结构

```text
.
├─ backend/               # FastAPI 后端
│  ├─ app/
│  ├─ requirements.txt
│  ├─ Procfile
│  └─ start.sh
├─ frontend-v2/           # React + Vite 前端
├─ data/                  # 本地/容器持久数据目录（SQLite）
└─ package.json
```

## 本地开发

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

### 前端

```powershell
cd frontend-v2
npm install
npm run dev -- --host 127.0.0.1 --port 8510 --strictPort
```

默认访问：

- 前端：`http://127.0.0.1:8510`
- 后端：`http://127.0.0.1:8766`

## Zeabur 部署

### 后端服务

- 服务类型：Python
- Root Directory：`backend`
- Install Command：`pip install -r requirements.txt`
- Start Command：`uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- 持久卷：挂载到项目 `data/`（用于 SQLite，避免重启丢失）

建议环境变量：

- `AUTH_JWT_SECRET`：JWT 签名密钥（必须）
- `APP_MASTER_KEY`：第三方 API Key 加密主密钥（必须）
- `CORS_ALLOW_ORIGINS`：允许跨域源，逗号分隔（必须设置为前端域名）
- `ONEAPI_BASE_URL`：OneAPI 服务根地址（例如 `https://oneapi.example.com`，不要额外拼 `/api` 两次）
- `ONEAPI_API_PREFIX`：OneAPI 接口前缀（默认 `/api`）
- `ONEAPI_V1_BASE_URL`：OneAPI 的 OpenAI 兼容地址（可留空，默认 `${ONEAPI_BASE_URL}/v1`）
- `SUBTITLE_GLOBAL_CONCURRENCY`：全局并发上限（默认 `3`）
- `SUBTITLE_PER_USER_CONCURRENCY`：单用户并发上限（默认 `1`）
- `URL_SOURCE_ALLOWED_DOMAINS`：URL 任务允许域名（默认 `youtube.com,youtu.be,bilibili.com,b23.tv`）
- `YT_DLP_EXECUTABLE`：`yt-dlp` 可执行路径（Zeabur 建议显式配置）
- `YT_DLP_COOKIES_FILE`：可选，`cookies.txt` 文件路径（B 站 412 风控时建议配置）
- `YT_DLP_BILIBILI_COOKIE`：可选，直接填写 B 站 Cookie 字符串（无需上传 cookies.txt，优先于 `YT_DLP_COOKIES_FILE`）
- `YT_DLP_SITE_COOKIE_MAP_JSON`：可选，按域名配置 Cookie（JSON，如 `{"bilibili.com":"SESSDATA=...; bili_jct=...","youtube.com":"SID=..."}`），对所有用户统一生效
- `YT_DLP_SITE_HEADER_MAP_JSON`：可选，按域名追加请求头（JSON，如 `{"example.com":{"Referer":"https://example.com"}}`）
- `YT_DLP_PROXY_POOL`：可选，代理池（逗号分隔或 JSON 数组），命中 412/429 等风控错误时自动轮换重试
- `YT_DLP_EXTRA_ARGS`：可选，附加给 `yt-dlp` 的参数字符串（如 `--extractor-retries 3 --retry-sleep extractor:exp=1:20`）
- `YT_DLP_USER_AGENT`：可选，下载请求 UA（默认内置 Chrome UA）
- `YT_DLP_BILIBILI_REFERER`：可选，B 站下载 Referer（默认 `https://www.bilibili.com/`）
- `YUTTO_EXECUTABLE`：可选，`yutto` 可执行路径（仅 B 站触发 412 时作为二级兜底）
- `PIP_CACHE_DIR`：建议设置为持久卷路径（如 `/data/pip-cache`），减少重复部署下载依赖时间
- `PIP_DISABLE_PIP_VERSION_CHECK=1`：关闭 pip 版本检查，缩短安装准备阶段

部署提速说明：

- 后端主依赖已精简到核心运行链路，移除了 `spacy/pandas/openpyxl/keybert/sentence-transformers/dashscope` 等重量包。
- 若后续需要启用对应可选能力，可在服务内额外安装这些包；未安装时不影响主流程，只会让健康检查中的可选能力标记为 `false`。

部署后运行依赖验收（`GET /api/v1/health`）：

- `capabilities.subtitle_dep_ffmpeg=true`
- `capabilities.subtitle_dep_ffprobe=true`
- `capabilities.subtitle_dep_ytdlp=true`

若 `ffmpeg/ffprobe=false`：在 Zeabur 添加系统包 `ffmpeg`。  
若 `ytdlp=false`：确认容器内 `yt-dlp` 可执行存在，必要时设置 `YT_DLP_EXECUTABLE`。
若需启用 B 站二级兜底：在后端容器安装 `yutto`（如 `pip install yutto`），必要时设置 `YUTTO_EXECUTABLE`。
若 B 站链接报 `HTTP 412 Precondition Failed`：系统会先自动走 `yt-dlp` 主链路，再触发代理池重试（`YT_DLP_PROXY_POOL`），最后尝试 `yutto` 兜底（若已安装并可执行）。建议至少配置 `YT_DLP_BILIBILI_COOKIE` 或 `YT_DLP_SITE_COOKIE_MAP_JSON`，并准备 `YT_DLP_COOKIES_FILE` 作为通用后备。

### 前端服务

- 服务类型：Node（静态站点）
- Root Directory：`frontend-v2`
- Node Version：`20.x`
- Install Command：`npm ci --include=dev`
- Build Command：`npm run build:zeabur`
- Output Directory：`dist`

前端环境变量（Zeabur）：

- `NPM_CONFIG_PRODUCTION=false`
- `NPM_CONFIG_INCLUDE=dev`
- `VITE_SUBTITLE_API_BASE=https://<你的后端域名>/api/v1`

部署建议（强烈推荐）：

- 使用前后端双服务：前端域名与后端域名分离
- 前端只负责静态页面；所有 API 请求通过 `VITE_SUBTITLE_API_BASE` 指向后端域名
- 后端 `CORS_ALLOW_ORIGINS` 必须包含前端完整源（例如 `https://english.preview.aliyun-zeabur.cn`）

说明：

- `vite/client` 类型错误通常来自未安装 `devDependencies`，因此前端必须使用 `npm ci --include=dev`
- 仓库已提供 `npm run predeploy:check` 与 `npm run build:zeabur` 作为部署前门禁（Node 20、`file:` 依赖、`vite/client` 类型解析、环境变量校验）
- `build:zeabur` 会严格校验 `VITE_SUBTITLE_API_BASE`：必须是非 localhost 的 http(s) 地址，且必须以 `/api/v1` 结尾

### ASR Admin 独立后台服务（推荐）

为满足“普通用户完全不可见、管理员独立管理”，新增独立管理服务：

- 服务类型：Python
- Root Directory：`backend`
- Install Command：`pip install -r requirements.txt`
- Start Command：`uvicorn app.admin_console_main:app --host 0.0.0.0 --port $PORT`

Admin 服务环境变量：

- `ONEAPI_BASE_URL`：OneAPI 服务根地址
- `ONEAPI_API_PREFIX`：OneAPI 接口前缀（默认 `/api`）
- `USER_BACKEND_API_BASE`：用户后端 API 基址（示例：`https://es-deploy-api.preview.aliyun-zeabur.cn/api/v1`）
- `ASR_ADMIN_SERVICE_TOKEN`：Admin 服务调用用户后端内部管理接口的共享令牌（必须，与用户后端一致）
- `ADMIN_SESSION_TTL_HOURS`：管理员登录态有效期（默认 `12`）

用户后端新增必须环境变量：

- `ASR_ADMIN_SERVICE_TOKEN`：与 Admin 服务一致；用于保护内部接口 `/api/v1/internal/asr-admin/*`

说明：

- 普通用户站点已移除 ASR 管理台入口与页面。
- 管理功能（全局用户、调账、倍率、路由、流水导出）仅在独立 Admin 服务提供。

## 鉴权与接口

认证接口：

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`

除 `health` 与 `auth` 外，其余业务接口均要求：

- `Authorization: Bearer <token>`

## 安全与策略

- 个人中心读取接口不再返回明文 `api_key`，仅返回 `api_key_masked` 与 `has_api_key`
- 密钥写入使用独立接口：`PUT /api/v1/profile/keys`
- URL 任务启用来源策略校验：
  - 拒绝 `localhost`、内网/保留网段、回环地址
  - 默认仅允许 YouTube/Bilibili 域名
- `browser-errors` 调试接口已提前下线（410）

## 验证命令

```powershell
# 前端构建
npm --prefix frontend-v2 run build

# 后端导入检查
python -c "import sys; sys.path.insert(0, 'backend'); import app.main as m; print('import-ok')"
```

线上验收（双服务）：

```powershell
# 1) 后端域名必须返回 JSON，不允许返回 HTML
curl https://<后端域名>/api/v1/health

# 2) 前端域名打开登录页后，抓包确认请求目标为 <后端域名>/api/v1/*
# 3) 前端域名若直接访问 /api/v1/health 返回 HTML，说明 API 仍命中了前端服务
```

部署前门禁（建议在 CI 或本地 Node 20 执行）：

```powershell
cd frontend-v2
$env:VITE_SUBTITLE_API_BASE="https://example.com/api/v1"
npm ci --include=dev
npm run predeploy:check
npm run build:zeabur
```

可选审计：

```powershell
cd backend
.\.venv\Scripts\python -m pip install pip-audit
.\.venv\Scripts\pip-audit
```
