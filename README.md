# 智能客服系统（Home Orbit 家庭助手 Agent 平台）

> FastAPI + Vue3 前后端分离的家庭助手平台：家庭空间多租户、DeepAgents 编排、多模型配置（OpenAI-compatible / Anthropic）、Agent/Skill 创作与审核市场、家庭日常闭环（厨师/计划员/百科/总管）、治理与审计。原扫地机器人客服能力整体迁为官方「设备管家」Agent。

---

## 架构总览

```
┌──────────────────────────┐        ┌──────────────────────────┐
│  前端 Vue3 + Element Plus │        │  管理后台（同一前端 /admin）│
│  Home Orbit 用户侧五栏    │        │  初始化/运营/审核/模型/审计 │
└────────────┬─────────────┘        └────────────┬─────────────┘
             │  /api（axios + fetch SSE）         │
             ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI 后端（:8000）                      │
│  /auth /chat(SSE) /households /home /creator /market        │
│  /reviews /models /tools /audit /ops /tickets /devices      │
│  JWT · 家庭角色（owner/admin/member/viewer）· 资源按家庭隔离   │
├─────────────────────────────────────────────────────────────┤
│  AgentCompiler → DeepAgents/受限流程 · ToolPolicy 授权/审批    │
│  ModelRegistry（多 Endpoint/Profile/用途绑定/配额）            │
├──────────────────────┬──────────────────────────────────────┤
│   MySQL 8（主）       │   Chroma 版本化向量集合                  │
│   SQLite（测试/预览） │   OpenAI-compatible / Anthropic         │
│   Redis + Celery     │   worker/beat：提醒·通知·FAQ·向量化      │
└──────────────────────┴──────────────────────────────────────┘
```

完整部署拓扑：`mysql(healthy) + redis(healthy) → backend(Alembic 迁移 + API) → celery-worker / celery-beat → frontend`。SQLite 仅用于 pytest、本地预览与单进程开发，**不启动 Celery**。

## 目录结构

```
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py          # 入口：建库/种子/CORS/路由/健康检查
│   │   ├── core/            # config（.env 统一配置）、security（JWT/bcrypt）
│   │   ├── api/             # auth chat knowledge tickets devices admin
│   │   ├── schemas/         # Pydantic 请求/响应模型
│   │   ├── db/              # models session repositories seed
│   │   └── services/        # chat_service sentiment title_gen followup faq_gen
│   ├── agent/               # DeepAgents 组装 + subagents + tools
│   ├── rag/vector_store.py  # Chroma（metadata 标记/按文件删除/重建）
│   ├── llm_factory/factory.py # OpenAI-compatible/Anthropic 模型工厂
│   ├── app/llm_runtime/     # 多模型运行时、配额与凭据
│   ├── prompts/             # 主代理 + 子代理提示词
│   ├── alembic/versions/    # 0015 迁移链（清库重建 Schema）
│   ├── tests/               # pytest（490+ 项，LLM 全 mock）
│   └── data/                # 运行产物（gitignore）：上传/向量库/SQLite
├── frontend/                # Vue3 前端（Dockerfile + nginx.conf）
│   └── src/                 # api stores router components views
├── data/                    # 仓库资产：知识库源文件 + external/records.csv
├── docker-compose.yml       # mysql + backend + frontend(nginx)
└── .env.example             # 环境变量模板
```

## 快速开始（Docker 一键启动）

```bash
cp .env.example .env        # 设置 LLM_CREDENTIAL_MASTER_KEY 等必要配置
docker compose up -d --build
```

- 前端：http://localhost （nginx 托管静态文件并反代 `/api`）
- Swagger 文档：http://localhost/docs
- MySQL 数据持久化在 `mysql-data` 卷；后端运行产物（上传/向量库/SQLite）在 `backend-data` 卷。
- `docker compose down` 停止（加 `-v` 同时清空数据卷）。

## 本地开发

后端（Python ≥ 3.11，示例 3.12）：

```bash
make setup                      # Python 虚拟环境 + npm ci；Python 3.14 环境可用 make setup PYTHON=python3.12
make backend-test
make frontend-test
make frontend-build
```

- `APP_MODE=production` 强制使用 MySQL，并拒绝缺失、默认或弱安全配置。
- 单进程本地预览可显式设置 `APP_MODE=preview` 与 SQLite `DATABASE_URL`；pytest 使用 `APP_MODE=test`。
- 测试 URL、API Key 只放在本地 `.env` 或 CI secrets 中，不要写入代码、示例文件或数据库迁移。
- Swagger：http://localhost:8000/docs ；健康检查：`GET /health`。

前端：

```bash
cd frontend
npm ci
npm run dev                    # http://localhost:5173，/api 自动代理到 8000
```

测试（490+ 项，从 backend 目录或仓库根均可）：

```bash
make test
```

## 种子账号

| 账号 | 初始密码 | 说明 |
|------|----------|------|
| admin | `ADMIN_INITIAL_PASSWORD`（生产必填，不少于 12 字符） | 管理员；**首次登录强制修改密码** |
| demo | `DEMO_INITIAL_PASSWORD`（生产必填，不少于 12 字符） | 演示用户，关联档案 user_id=1001，含样例使用记录 |

种子在启动时幂等执行；普通注册用户角色为 `user`。

## API 概览（前缀 /api，完整文档见 /docs Swagger）

| 模块 | 端点 |
|------|------|
| 认证 | `POST /auth/register` `POST /auth/login` `POST /auth/refresh` `GET /auth/me` `PUT /auth/password` |
| 会话 | `GET/POST/PATCH/DELETE /chat/sessions`、`GET /chat/sessions/:id/messages`、`POST /chat/sessions/:id/chat`（SSE 流式）、`POST /chat/messages/:id/feedback` |
| 工单（用户） | `GET /tickets/my`、`POST /tickets`、`GET /tickets/my/:id` |
| 设备 | `GET /devices`、`GET /devices/:model_id`、`POST /devices/compare`、`POST /devices/restock-notify/:model_id` |
| 知识库（admin） | `GET/POST/DELETE /knowledge/files`、`POST /knowledge/rebuild`、`POST /knowledge/files/:id/generate-faq`、`GET /knowledge/faq`、`PUT /knowledge/faq/:id/review` |
| 管理（admin） | `GET /admin/dashboard`、账号管理、设备/库存 CRUD、缺货登记、工单处理 |

SSE 事件序列：`token`（增量输出）→ `run`（运行状态）→ `tool`（工具状态）→ `sentiment`（情绪）→ `followup`（推荐追问）→ `approval_required` / `approval_resumed`（高风险确认）→ `done`；前端 aria-live 仅按段落/完成播报。

## 模型初始化向导（首次启动必经）

1. 设置环境变量 `LLM_CREDENTIAL_MASTER_KEY`（Base64 的 32 字节随机密钥）后以 admin 登录，进入 `/admin/setup`。
2. 创建 Endpoint（OpenAI-compatible 或 Anthropic；base URL 防 SSRF 校验）并提交 API Key —— 密钥以 AES-256-GCM 加密入库，页面仅显示末四位；数据库无明文。
3. 创建模型档案并跑完整能力测试（生成/SSE/取消/工具调用/结构化输出；embedding 为维度测试），全部通过后绑定用途：`primary_chat`（主聊天）、`embedding`（向量，必须 OpenAI-compatible）、可选 `auxiliary`（辅助生成）与 `review_model`（审核风险摘要）。
4. 主聊天与 embedding 绑定通过前，所有 AI 接口稳定返回 `MODEL_NOT_CONFIGURED`，前端显示初始化引导；非 AI 家庭/账号功能不受影响。
5. 为家庭配置月度配额与模型授权（`/admin` 用户与家庭 / 模型治理），用户聊天走配额生命周期（预约→完成/失败/释放）。

## 功能地图

- **家庭空间（households）**：注册创建默认家庭；owner/admin/member/viewer 角色；邀请加入；所有资源（会话、任务、清单、知识、安装、通知、审计查询）按家庭隔离，跨家庭一律 403/404 且无存在性泄露。
- **家庭日常闭环（home）**：官方 Agent 设备管家/家庭厨师/家庭计划员/生活百科/家庭总管；购物清单、任务、周期提醒、通知中心、备忘、结构化偏好（仅建议+确认）；Celery 到期派发幂等可重试。
- **自然语言创作（creator）**：一句话 → 意图卡 → Skill/Tool 匹配 → 权限声明 → 知识上传（扫描后向量化）→ 沙盒试运行（配额计量）→ 私用发布或提交公开审核；只允许声明式配置，禁止代码/URL/连接器。
- **市场与审核（market/reviews）**：发现/搜索/安装/收藏/举报；安装固定审核版本 + 逐项工具授权（默认拒绝）；公开发布走自动预检（Schema/权限/敏感内容/提示注入/预算/试运行）+ 审核 LLM 风险摘要 + 管理员人工终审；版本升级需重新授权新增权限；下架保留既有安装运行。
- **治理与审计（ops/audit）**：运营概览、模型用量与对账、家庭配额治理、超限事故、Celery 任务运营、Agent Run / Tool Execution 查询、部署指针与模型配置版本、管理员动作全量审计（载荷脱敏）。

## 验证命令

```bash
# 后端全量测试（LLM 全 mock，含多租户矩阵/安全/并发验收套件）
make backend-test

# 前端测试与生产构建
make frontend-test frontend-build

# Docker 全栈（mysql healthcheck → 迁移 → API → worker/beat → frontend）
make docker-up
```

## 家庭日常闭环（Phase 5）

- 官方 Agent：设备管家、家庭厨师（菜谱/按人数换算/采购清单）、家庭计划员（家务计划/周期提醒/任务确认）、生活百科（家庭隔离知识检索）、家庭总管（委派上述官方子代理）。
- 内置工具目录 `household.*`：读低风险、写可撤销（清单写入、任务/备忘、提醒创建/取消、偏好建议）；结构化偏好仅 Agent 建议 + 家庭成员确认写入，无自由文本自动记忆。
- `/api/home/*`：`/today`（今日任务/提醒/清单汇总/未读通知/设备异常）、`/shopping-list`、`/tasks`、`/reminders`、`/notifications`（按账号已读）、`/memos`、`/preferences`（含 proposals confirm/reject）、`/device-anomalies`；Home Orbit 首页接入真实家庭状态。

## 异步任务与 Celery

- 任务类型：到期提醒派发、站内通知派发、知识向量化、FAQ 生成、沙盒试运行（本阶段仅声明式校验）、模型健康检查。
- 每条任务以幂等键入库（`celery_tasks`：status/attempt/max_attempts/next_retry_at/error_code/audit）；瞬时失败按指数退避重试，重试耗尽进入 dead 并生成站内通知；副作用按幂等键去重（提醒按 occurrence、通知按 source_id）。
- 完整部署：`docker compose up` 启动 `redis`、`celery-worker`、`celery-beat`（含健康检查与依赖顺序），beat 每 30 秒派发到期提醒并处理到期重试，每小时执行模型健康检查。
- SQLite 预览/测试不启动 Celery。手动触发边界（测试与运维文档化入口）：
  - Python：`backend.app.tasks.manual.trigger(session_factory, task_type, payload, idempotency_key)`、`beat_tick(session_factory)`、`process_retries(session_factory)`；
  - 管理端 API：`GET /api/admin/tasks`、`POST /api/admin/tasks/{task_type}/run`。

## 配置项（.env / 环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_CREDENTIAL_MASTER_KEY` | 无 | Base64 编码的 32 字节主密钥；模型 API Key 由管理员向导以 AES-256-GCM 加密入库 |
| `APP_MODE` | `production` | `production` 强制 MySQL 与 Alembic head；`preview`/`test` 才允许显式 SQLite |
| `DATABASE_URL` | `mysql+pymysql://root:password@localhost:3306/customer_service` | 数据库连接；不会因连接失败自动切换 |
| `JWT_SECRET` | 开发默认值 | JWT 签名密钥；生产必须不少于 32 字符 |
| `ADMIN_INITIAL_PASSWORD` | 开发默认值 | admin 种子初始密码；生产不少于 12 字符 |
| `DEMO_INITIAL_PASSWORD` | 开发默认值 | demo 种子初始密码；生产不少于 12 字符 |
| `CORS_ORIGINS` | `http://localhost:5173` | 允许的跨域来源，逗号分隔 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | 无 | docker-compose 中 MySQL 密码；生产部署必填 |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis/Celery broker（完整部署必填；SQLite 模式不使用） |
| `CELERY_RETRY_BACKOFF_SECONDS` | `30` | 任务重试退避基数（指数退避） |
| `FOLLOWUP_TIMEOUT_SECONDS` | `4` | SSE 追问生成等待秒数，超时降级为空数组 |

## 常见问题

**聊天等 AI 功能返回 `MODEL_NOT_CONFIGURED`**
确认 `LLM_CREDENTIAL_MASTER_KEY` 有效，并由管理员在初始化向导中创建 Endpoint、加密凭据、通过完整能力测试并绑定主聊天与 embedding 用途。非 AI 接口在初始化前仍可使用。

**Docker 下 SSE 消息不流式/卡住**
nginx 已对 `/api` 设置 `proxy_buffering off`、`proxy_read_timeout 3600s` 与 HTTP/1.1 长连接；若自建反向代理，需做同样配置（后端响应亦携带 `X-Accel-Buffering: no`）。

**本地单进程预览不想安装 MySQL**
显式设置 `APP_MODE=preview` 和 `DATABASE_URL=sqlite:///backend/data/customer_service.db`。该模式仅用于预览，不运行 Celery；完整部署必须使用 MySQL + Redis 并先执行 Alembic migration。

**admin 首次登录被要求改密**
种子账号带 `must_change_password` 标记，登录后前端强制跳转改密页；修改成功后恢复正常。
