# English Studying（读写练习）

一个本地优先的英语学习应用，当前聚焦 **听力练习 + 学习数据中心**。

当前项目为：
- 前端（主入口）：`frontend-v2/`（React + Vite + Shadcn New York / Neutral）
- 后端：`backend/` FastAPI（字幕任务、历史记录）
- 旧前端目录：`src/`（legacy，非主入口，进入下线观察）

---

## 运行方式（推荐）

### 一键启动（Windows）
在项目根目录双击：

```bat
00_一键启动听力.bat
```

该脚本内置完整启动流程，便于在资源管理器中快速定位启动文件。

脚本会自动：
1. 创建并安装后端虚拟环境 `backend/.venv`
2. 安装 `frontend-v2` 依赖
3. 清理占用端口 `8766/8510` 的旧进程
4. 启动后端：`127.0.0.1:8766`
5. 启动前端：`frontend-v2` Vite dev server `127.0.0.1:8510`
6. 打开浏览器：`http://localhost:8510/listening`

注意：脚本会强制结束占用 `8766/8510` 的进程，请避免在这两个端口运行无关服务。

### 手动启动（可选）

```powershell
# 1) 后端
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8766

# 2) 前端（新终端）
cd ..\frontend-v2
npm install
npm run dev -- --host 127.0.0.1 --port 8510 --strictPort
```

---

## 入口说明

- 主前端：`http://localhost:8510/listening`
- 数据中心：`http://localhost:8510/dashboard`

---

## 后端 API（当前）

基础：
- `GET /api/v1/health`

历史记录：
- `GET /api/v1/history-records`
- `PUT /api/v1/history-records`

字幕任务：
- `POST /api/v1/subtitle-jobs`
- `POST /api/v1/subtitle-jobs/from-url`
- `POST /api/v1/subtitle-jobs/resume-llm`
- `GET /api/v1/subtitle-jobs/{job_id}`
- `GET /api/v1/subtitle-jobs/{job_id}/result`
- `GET /api/v1/subtitle-jobs/{job_id}/video`
- `DELETE /api/v1/subtitle-jobs/{job_id}`

自动字幕配置探测：
- `POST /api/v1/subtitle-config/test`
- `POST /api/v1/subtitle-config/test-llm`
- `POST /api/v1/subtitle-config/test-whisper`
- `GET /api/v1/whisper/local-models`

错误报告：
- `POST /api/v1/browser-errors`
- `GET /api/v1/browser-errors/read`
  - 说明：以上接口为调试遗留能力，已进入 14 天下线观察窗口（2026-02-26 至 2026-03-12），响应头会返回 `X-Deprecated`。

---

## 数据与存储

本地主要存储：
- `localStorage`：学习统计、目标配置、UI 状态
- `IndexedDB`：`ListeningPracticeDB`（含 `files`、`translations`）

迁移原则：
- 不改后端 API 签名
- 不改既有本地键名
- v2 前端通过兼容层读写旧数据

---

## 测试

### 根项目逻辑测试

```powershell
node --test tools/tests/subtitle-parser.test.mjs
node --test tools/tests/subtitle-import-pipeline.test.mjs
node --test tools/tests/listening-upload-v2-flow.test.mjs
```

### v2 前端构建检查

```powershell
npm --prefix frontend-v2 run build
```

---

## 项目结构（简化）

```text
.
├─ frontend-v2/                   # 主前端（React + Vite）
├─ src/                           # legacy 目录（非主入口，待下线）
├─ backend/                       # FastAPI 后端
├─ tools/tests/                   # 测试脚本
├─ 00_一键启动听力.bat            # 一键启动入口（推荐）
└─ CHANGELOG.md
```

---

## 说明

- 项目处于持续迭代中，具体改动请查看 `CHANGELOG.md`。
- 以仓库当前代码为准，历史文档描述若有冲突，请以代码行为优先。
