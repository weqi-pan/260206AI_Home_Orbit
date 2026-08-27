# 智能客服系统（扫地机器人）

> FastAPI + Vue3 前后端分离的智能客服系统：DeepAgents 多子代理调度、RAG 知识库、JWT 认证、工单闭环与管理后台。

---

## 架构总览

```
┌──────────────────────────┐        ┌──────────────────────────┐
│  前端 Vue3 + Element Plus │        │  管理后台（同一前端 /admin）│
│  Vite · Pinia · Router   │        │  用户/知识库/库存/工单/看板 │
└────────────┬─────────────┘        └────────────┬─────────────┘
             │  /api（axios + fetch SSE）         │
             ▼                                   ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI 后端（:8000）                      │
│  /auth  /chat(SSE)  /tickets  /devices  /knowledge  /admin   │
│  JWT 认证 · 角色权限 · 满意度/情绪/追问/标题 LLM 辅助服务       │
├─────────────────────────────────────────────────────────────┤
│                DeepAgents 主代理 + 3 子代理                   │
│  售前设备顾问 │ 售后知识专家 │ 报告与工单                       │
│  TodoList 规划 · 工具监控中间件 · runtime context 身份注入     │
├──────────────────────┬──────────────────────────────────────┤
│   MySQL 8（主）       │   Chroma 向量库 + DashScope Embeddings │
│   SQLite（自动兜底）  │   通义千问 qwen3-max / qwen-flash       │
└──────────────────────┴──────────────────────────────────────┘
```

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
│   ├── model/factory.py     # ChatTongyi 模型工厂
│   ├── prompts/             # 主代理 + 子代理提示词
│   ├── tests/               # pytest（67 项，LLM 全 mock）
│   └── data/                # 运行产物（gitignore）：上传/向量库/SQLite
├── frontend/                # Vue3 前端（Dockerfile + nginx.conf）
│   └── src/                 # api stores router components views
├── data/                    # 仓库资产：知识库源文件 + external/records.csv
├── docker-compose.yml       # mysql + backend + frontend(nginx)
└── .env.example             # 环境变量模板
```

## 快速开始（Docker 一键启动）

```bash
cp .env.example .env        # 填入 DASHSCOPE_API_KEY 等必要配置
docker compose up -d --build
```

- 前端：http://localhost （nginx 托管静态文件并反代 `/api`）
- Swagger 文档：http://localhost/docs
- MySQL 数据持久化在 `mysql-data` 卷；后端运行产物（上传/向量库/SQLite）在 `backend-data` 卷。
- `docker compose down` 停止（加 `-v` 同时清空数据卷）。

## 本地开发

后端（Python ≥ 3.11，示例 3.12）：

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows；Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cd ..
uvicorn backend.app.main:app --reload --port 8000
```

- 无 MySQL 时自动降级 SQLite（`backend/data/customer_service.db`），功能完全一致。
- Swagger：http://localhost:8000/docs ；健康检查：`GET /health`。

前端：

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173，/api 自动代理到 8000
```

测试（67 项，从 backend 目录或仓库根均可）：

```bash
cd backend && python -m pytest          # 或在仓库根：python -m pytest backend
```

## 种子账号

| 账号 | 初始密码 | 说明 |
|------|----------|------|
| admin | `ADMIN_INITIAL_PASSWORD`（默认 `Admin@123`） | 管理员；**首次登录强制修改密码** |
| demo | `Demo@1234`（`DEMO_INITIAL_PASSWORD`） | 演示用户，关联档案 user_id=1001，含样例使用记录 |

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

SSE 事件序列：`token`（增量输出）→ `tool`（工具状态）→ `sentiment`（情绪）→ `followup`（推荐追问）→ `done`。

## 配置项（.env / 环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DASHSCOPE_API_KEY` | 无（必填） | 通义千问 API Key，缺失时后端启动即报错 |
| `DATABASE_URL` | `mysql+pymysql://root:password@localhost:3306/customer_service` | 主数据库；连接失败自动降级 SQLite |
| `SQLITE_FALLBACK_URL` | `sqlite:///backend/data/customer_service.db` | SQLite 兜底库位置 |
| `JWT_SECRET` | 开发默认值（告警） | JWT 签名密钥，生产必须设置 |
| `ADMIN_INITIAL_PASSWORD` | `Admin@123` | admin 种子初始密码 |
| `DEMO_INITIAL_PASSWORD` | `Demo@1234` | demo 种子初始密码 |
| `CORS_ORIGINS` | `http://localhost:5173` | 允许的跨域来源，逗号分隔 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | `change-me-root` / `change-me` | docker-compose 中 MySQL 密码 |

## 常见问题

**启动报“聊天模型不可用：未配置 DASHSCOPE_API_KEY”**
在 `.env` 或环境变量中设置有效的通义千问 API Key（[获取地址](https://dashscope.console.aliyun.com/)）。

**Docker 下 SSE 消息不流式/卡住**
nginx 已对 `/api` 设置 `proxy_buffering off`、`proxy_read_timeout 3600s` 与 HTTP/1.1 长连接；若自建反向代理，需做同样配置（后端响应亦携带 `X-Accel-Buffering: no`）。

**不想装 MySQL**
不设置 `DATABASE_URL`（或 MySQL 不可达）时后端自动降级 SQLite，日志输出“MySQL 连接失败，自动降级 SQLite”。

**admin 首次登录被要求改密**
种子账号带 `must_change_password` 标记，登录后前端强制跳转改密页；修改成功后恢复正常。
