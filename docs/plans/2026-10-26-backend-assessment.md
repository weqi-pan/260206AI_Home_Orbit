# 后端框架概览与功能完成度评估

> 本文为 **2026-10-26 的只读评估**，仅阅读代码与运行只读命令，未改动任何文件。
> 数据口径：`backend/` 排除 `.venv` 后共 227 个 Python 文件 / 28,637 行；业务代码（`app agent rag llm_factory`）149 文件 / 13,944 行，`tests` 61 文件 / 13,376 行，`alembic` 17 文件 / 1,317 行 —— 测试代码与业务代码几乎 1:1。
> 修复计划见 [2026-10-26-backend-fix-plan.md](2026-10-26-backend-fix-plan.md)。

## Context

前端五轮优化（PR #1–#8）已全部合入 master 且 CI 全绿，本次转向后端：摸清技术栈、代码组织、数据层、API 表面与核心的 Agent/LLM 运行时，并给出「功能做完了没有、还差什么」的量化结论。

结论先行：**业务功能面基本齐全**（20 个路由、149 个端点、62 张表、5 个官方 Agent、26 个内置工具、511 项测试），**模型治理与审计做得扎实**，但**平台工程层（限流、日志、可观测性、家庭成员管理）和错误契约一致性明显欠账**，另有若干死代码与同步阻塞点。本文档只做评估与排序建议，不改任何代码。

---

## 一、技术栈

| 层 | 选型 | 版本 |
|---|---|---|
| Web | FastAPI + uvicorn（ASGI） | fastapi 0.116.1 固定 |
| 流式 | `sse-starlette` `EventSourceResponse` | ≥3.0.2 |
| Agent | DeepAgents + LangGraph + LangChain | deepagents 0.7.9 固定，langgraph ≥1.2.11 |
| ORM | SQLAlchemy 2.0 typed declarative（`Mapped`/`mapped_column`） | ≥2.0.36 |
| 迁移 | Alembic，16 个线性 revision | 0016 为 head |
| 主库 | MySQL 8.4（PyMySQL） | 生产强制 |
| 测试/预览库 | SQLite | 仅 `app_mode in {test, preview}` |
| 向量库 | Chroma（langchain-chroma），按 household + skill_version 过滤 | — |
| 队列 | Celery + Redis（broker+backend）、beat | celery ≥5.4 |
| 认证 | 自研 PyJWT HS256 + bcrypt(rounds=12)，无第三方 auth 框架 | — |
| 校验 | Pydantic v2 + pydantic-settings | — |

**值得注意的架构选择**：模型配置**完全由数据库驱动**，没有任何 env 里的模型配置。[factory.py](backend/llm_factory/factory.py) 里 `get_chat_model`/`get_aux_model` 是故意抛 `MODEL_NOT_CONFIGURED` 的桩 —— 真正的解析走 `ModelRuntimeService.resolve`。这是一条清晰的边界，但也留下一套部分无用的 V1 执行栈（见问题 #12）。

---

## 二、代码组织

```
backend/
  app/
    main.py              工厂 create_app + lifespan + 20 个路由注册（111 行，很干净）
    core/                config（Settings + 生产配置校验）、security（JWT/bcrypt/依赖）
    api/                 50 文件，20 个 router，子包用 __init__ 定义 APIRouter 后 star-import
    audit/ ops/          审计与运营查询（api + service）
    db/                  6 个模型文件 + session（revision 门禁）+ seed
    llm_runtime/         13 文件：凭证加密、SSRF、适配器、能力探针、配额、集合切换
    creator/ market/ review/   三大业务域服务层
    services/            日常闭环、情感、追问、标题、FAQ、工单摘要
    tasks/               celery_app / definitions / executor / manual / worker_*
  agent/
    deep_agent.py        V1 DeepAgents 组装（部分死代码）
    platform/            生产路径：compiler/policy/authority/approvals/snapshots/checkpointer/runner
    subagents/ tools/
  rag/vector_store.py
  alembic/versions/      0001 → 0016
  tests/                 61 文件 / 511 用例
```

分层意图清楚：`api → service → db`，Agent 执行单独在 `agent/platform`。**路由子包用 `from base import *` 的技巧**（如 [stream.py:1](backend/app/api/chat/stream.py#L1)）省了不少样板，代价是静态分析看不到依赖来源。

---

## 三、数据层

**62 张表**，集中 re-export 自 [models.py:222-236](backend/app/db/models.py#L222)，分 6 个模块：

- **核心**（`models.py`）：users / accounts / chat_sessions / tickets / knowledge_files / faq_pairs / devices / inventory / restock_notifications / device_usage_records / conversation_history / admin_action_audits
- **家庭**（`household_models.py`）：households / household_members / household_preferences / invitations —— **全库唯一使用 ORM `relationship()` 的地方**
- **日常闭环**（`household_daily_models.py`）：shopping_list_items / household_memos / household_preference_proposals
- **平台**（`platform_models.py`，506 行）：llm_endpoints / llm_credentials / llm_models / model_purpose_bindings / model_health_checks / tool_definitions / model_grants / usage_quotas / model_usage / quota_incidents / embedding_collection_builds / tool_permissions / skills / skill_versions / agent_definitions / agent_versions / agent_*_bindings / agent_deployments / agent_installations / agent_runs / tool_executions / approval_requests / agent_checkpoints / household_tasks / task_schedules / notifications / celery_tasks
- **创作者 / 市场**：creator_knowledge_assets / creator_trial_reports；market_entries / market_installs / review_submissions / review_checks / review_decisions / favorites / reports

**多租户实现方式值得单独说**：不用数据库 RLS，而是**复合外键 + `UniqueConstraint("id","household_id")` + `TenantRow` mixin**（[platform_models.py:89](backend/app/db/platform_models.py#L89)），让 `chat_sessions` 之类的行在 schema 层就不可能跨家庭错配。这比「查询时记得加 household_id」强得多，属于加分设计。

**启动门禁**：[session.py:13](backend/app/db/session.py#L13) `REQUIRED_DATABASE_REVISION = "20260828_0016"`，**精确相等**校验。安全但僵硬 —— 滚动发布必须整体切换，任何新 revision 都要同步改这个常量。

---

## 四、API 表面

**149 个 `@router.*` 声明 + `/health` = 150**，20 个 router 全部挂在 `/api`（[main.py:83-102](backend/app/main.py#L83)）。

| 前缀 | 端点数 | 主要能力 |
|---|---:|---|
| `/home` | 26 | orbit/today、任务、清单、备忘、提醒、偏好（含 Agent 建议确认）、通知 |
| `/creator` | 17 | 草稿 CRUD、NL 生成、校验、知识上传、沙盒试跑、发布/提交审核 |
| `/admin` | 17 | 账号、设备、家庭、库存、工单、Celery 任务、dashboard |
| `/models` | 13 | endpoint / profile / 能力探针 / 用途绑定 / 家庭授权 / 配额 |
| `/market` | 10 | 发现、详情、安装、升级、卸载、收藏、举报 |
| `/audit` | 10 | agent-runs / tool-executions / model-usage / celery / admin-actions / 发布指针 |
| `/chat` | 7 | 会话 CRUD、历史、反馈、**SSE 流式** |
| `/knowledge` | 7 | 上传、删除、重建、FAQ 生成与审核（admin） |
| `/reviews` | 6 | 队列、证据包、决策、下架、举报处理（admin） |
| `/auth` | 5 | register / login / refresh / me / password |
| `/ops` | 5 | 概览、家庭配额、超限事故、任务类型 |
| 其余 9 个 | ≤4 | households、devices、tickets、tools、agents、skills、installations、approvals、setup |

**权限模型**：`/models /knowledge /agents /skills /reviews /audit /ops` 在 router 层 `Depends(require_admin)`；用户侧路由逐端点校验 `get_current_account` + 家庭 membership + 角色（owner/admin/member/viewer）。`/health`、`/auth/register`、`/auth/login` 是唯一公开端点。

---

## 五、Agent / LLM 运行时 —— 项目真正的差异化

这块的质量明显高于其他部分：

**模型解析链**（[runtime_service.py:38-83](backend/app/llm_runtime/runtime_service.py#L38)）：binding → model → endpoint → credential → 用途类型匹配 → **健康检查必须 passed** → **revision 必须与当前一致** → 家庭必须有 grant。每步失败都给稳定码（`MODEL_NOT_CONFIGURED` / `MODEL_CAPABILITY_NOT_VERIFIED` / `MODEL_CONFIGURATION_STALE` / `MODEL_NOT_GRANTED`），并且返回冻结的 `ModelRuntimeSnapshot`，让一次 run 钉死在某个配置修订上。

**凭证加密**（[credentials.py:21-49](backend/app/llm_runtime/credentials.py#L21)）：AES-256-GCM，AAD 绑定 `endpoint_id + protocol + key_version` —— 把密文在端点之间搬家会直接校验失败。库里只存 `last_four`，任何 API 都不返回明文。

**SSRF 防护**（[endpoint_security.py](backend/app/llm_runtime/endpoint_security.py)）：不只拒绝私网/环回/link-local 解析结果，还用 `PinnedNetworkBackend` 把**已校验的 IP** 用于真实 TCP 连接 —— 这是防 DNS rebinding 的正确做法，很多项目只做了第一步。

**工具授权三层**：`ExecutionAuthority.recheck` 在**每次工具调用时**重新校验安装/版本/权限（[authority.py:60-64](backend/agent/platform/authority.py#L60)）；`PlatformToolExecutor` 对 `risk_level == "high"` 自动创建 `ApprovalRequest` 并中断图（[policy.py:54-68](backend/agent/platform/policy.py#L54)）；每次执行都写 `ToolExecution` 审计（成功/失败/拒绝/待审批四种状态）。

**配额**（[quotas.py:41-92](backend/app/llm_runtime/quotas.py#L41)）：先对配额行加写锁再读限额，从根上避免并发超卖；每次拒绝都落 `QuotaIncident`。

**26 个内置工具**（[catalog.py](backend/agent/platform/catalog.py)）：`device.*` 9 个、`household.*` 12 个、`platform.*` 4 个、`household.recipe.scale`。`GET /api/tools/catalog` 会把 DB 目录与代码目录比对，不一致直接 `TOOL_CATALOG_MISMATCH` 崩 —— 防止漂移的硬约束，很少见。

---

## 六、异步任务

[celery_app.py](backend/app/tasks/celery_app.py) 强制生产模式 + MySQL + Redis 才肯构建；SQLite 模式完全不启动 Celery。beat 调度：提醒派发 30s、重试处理 30s、模型健康检查每小时。

真正的设计亮点是 **broker 无关的 `TaskExecutor`**（[executor.py](backend/app/tasks/executor.py)）：幂等键入库、指数退避重试、耗尽进 dead 并生成站内通知。因为执行器不依赖 broker，测试和 SQLite 预览能用**同步方式**完整验证异步语义 —— 这解释了为什么 511 个测试能覆盖 Celery 相关逻辑却不需要 Redis。

---

## 七、测试

**61 文件 / 511 用例**（`pytest --collect-only` 实测），`grep "def test_"` 得 441，差额来自参数化。

做得好的：三个 acceptance 套件（security / matrix / concurrency）专门测多租户越权矩阵、流中断不泄露、并发不超卖；每个 migration 都有 schema 断言；审计完整性有独立 sweep 测试；`conftest.py` 每轮独立临时目录 + 强制 `APP_MODE=test` + autouse 删除主密钥（默认走未配置路径）。

缺口：

- **LLM 全 mock**，没有一次真实 provider 调用 —— Anthropic/OpenAI 的线协议、真实 SSE、Chroma 持久化都只经过 fake。
- **全部跑 SQLite**，MySQL 的 `FOR UPDATE` 行锁语义、`utf8mb4` 行为未经测试 —— 而配额正确性恰恰依赖行锁。
- 无覆盖率门禁（`pytest.ini` 只有 `testpaths`）、无 ruff/mypy、无依赖 lock 文件（只有 `>=x,<y` 区间）。
- `skills` 路由 0 测试引用。

---

## 八、功能完成度评估

| 域 | 完成度 | 判断依据 |
|---|---|---|
| 认证与账号 | 80% | 核心流程齐全；缺登出、令牌吊销、刷新轮换、找回密码、邮箱验证 |
| 家庭与成员 | 65% | 创建/邀请/接受可用；**完全没有成员列表接口外的管理**：无改角色、移出、转让、退出、邀请撤销 |
| 家庭日常闭环 | 95% | 7 组资源 + 聚合端点 + 幂等派发，闭环真正跑通；缺分页 |
| 聊天与 SSE | 90% | 配额生命周期、快照捕获、断连持久化都做了；缺消息长度上限、session 分页、重新生成 |
| 创作者工作室 | 95% | NL → 意图卡 → 校验 → 沙盒 → 发布/提审全链路；`REVIEW_SUBMISSION_LOCKED` 编辑锁到位 |
| 市场 | 90% | 安装/升级/卸载/收藏/举报齐全，版本不可变副本；搜索与分页是短板 |
| 审核治理 | 95% | 8 类预检 + LLM 风险摘要（仅建议，不写状态）+ 人工终审 + 状态机转移表，设计很克制 |
| 模型治理 | 98% | 见第五节，这是全项目最扎实的部分 |
| 审计与运营 | 90% | 读操作自审计、采纳脱敏、真实聚合 SQL；缺 retention 与导出 |
| 设备（业务） | 60% | 目录/搜索/对比/库存/缺货通知齐全，但**纯 seed 数据**，没有真实设备接入/配对/绑定 |
| 可观测性 | 15% | 见问题 #4 |
| 平台工程 | 35% | 无限流、无日志配置、无 metrics、依赖不锁、无静态检查 |

**总体判断：作为可演示、可评审的产品原型，功能面已经完整且深度不错；作为要上生产的系统，平台工程层和若干安全边界还需要一轮补齐。**

---

## 九、发现的问题（按严重度）

### 高

**1. 认证侧完全没有防护**

- 全项目 `rate_limit|slowapi|limiter` 零命中，**登录/注册/刷新无限流、无失败锁定、无失败审计**（[auth.py:66-70](backend/app/api/auth.py#L66) 只做 bcrypt 校验）。配合 `POST /auth/register` 对已存在用户名返回 409，构成账号枚举 + 撞库面。
- **无登出接口、无 jti denylist、无 refresh 轮换**；改密码后旧令牌依然有效（`PUT /auth/password` 只换 hash）。

**2. 高危工具审批的有效期由客户端决定**

[agent_platform.py:22](backend/app/schemas/agent_platform.py#L22) `ApprovalDecisionRequest.expires_at: datetime` 必填，但 [approvals.py:48-53](backend/app/api/approvals.py#L48) 只校验「必须在未来」。调用方可以批一个十年有效的窗口，绕过「高风险需逐次确认」的设计意图。应改为服务端上限（如 10 分钟）+ 固定枚举。

### 中

**3. 错误契约分裂**

161 处 `HTTPException` 中 **121 处用 `detail={"code": ...}`，40 处用纯中文字符串**，集中在 [knowledge.py](backend/app/api/knowledge.py)（8）、[households.py](backend/app/api/households.py)（6）、[auth.py](backend/app/api/auth.py)（6）、[admin/accounts.py](backend/app/api/admin/accounts.py)（4）。更关键的是 **`main.py` 没有注册任何 exception handler** —— Pydantic 校验失败的 422 是 FastAPI 默认的 `{"detail": [...]}` 数组形态，完全绕过 code 契约。前端 [extractErrorMessage](frontend/src/api/http.ts#L13) 虽然能兜住数组，但「稳定错误码」这个卖点在最常见的 422 上就断了。

**4. 可观测性基本为空**

无 `logging.basicConfig`/`dictConfig`、无 request id / correlation id、无 `/metrics`、无链路耗时。全后端只有 11 处 `logger.` 调用、5 处 `getLogger`。`/health` 返回静态 `{"status": "ok"}`（[main.py:104-106](backend/app/main.py#L104)），不探 DB、不探 Redis、不探迁移版本 —— 容器健康检查会一直绿。

**5. 家庭成员管理缺失**

[households.py](backend/app/api/households.py) 只有 4 个端点。**没有**：成员列表（只能靠 `GET /{id}` 顺带）、改角色、移出成员、转让所有权、退出家庭、撤销邀请、查看待处理邀请。更麻烦的是 [households.py:80-97](backend/app/api/households.py#L80)：接受邀请会**硬删**原 membership，若原家庭因此变空就**连家庭一起删**，无确认无回收。`请先转移原家庭所有权` 这个报错指向的操作根本不存在。

**6. viewer 在两处安装入口待遇不一致**

`POST /api/market/entries/{id}/install` 要求 owner/admin（[market/service.py:247](backend/app/market/service.py#L247)）；`POST /api/installations` 只校验 membership（[installations.py:32](backend/app/api/installations.py#L32)），viewer 可以通过。同类角色判定分散在 5 个文件里各写一遍，这种漂移会继续发生。

**7. 市场安装在请求内同步跑向量化**

[market/service.py:406-408](backend/app/market/service.py#L406) `executor.submit(...)` 紧接着 `executor.run(key)` —— 在请求线程里同步执行，生产环境也一样。这与「向量化走 Celery」的架构说明矛盾，安装延迟直接等于向量化耗时。

**8. 市场搜索是全量 Python 过滤**

[market/service.py:46-57](backend/app/market/service.py#L46)：先 `select` 出所有 published 行，再在内存里 `if query in row.name`。无 `LIKE`、无分页，条目增长后线性退化。

**9. 官方 Agent 按中文显示名查找**

[chat/base.py:83-92](backend/app/api/chat/base.py#L83) 和 [sessions.py:10-24](backend/app/api/chat/sessions.py#L10) 都用 `AgentDefinition.name == "设备管家"` 定位。改名、加语言或做多语言就静默失效 —— 应该用稳定的 authority slot 或 key。

**10. 列表端点普遍无分页**

chat sessions/messages、`/home/*` 全部列表、`/tickets/my`、`/market/my`、`/market/favorites` 都没有 limit/offset。只有 `/audit` 和 `/ops` 实现了带 clamp 的分页。

### 低

**11. 死代码：不可达的 except**

[admin/operations.py:106](backend/app/api/admin/operations.py#L106) `except PermanentTaskError` 永远不触发 —— [executor.py:118-125](backend/app/tasks/executor.py#L118) 在内部就 catch 掉并返回 record。未知 task_type 打这个接口会得到 202 风格的「成功」响应体，错误藏在 `status` 里。

**12. 遗留 V1 Agent 栈**

`llm_factory.get_chat_model` 抛错、`UnavailableAgentRunner` 名字像降级实现实际是主路径（[defaults.py:6-25](backend/app/services/defaults.py#L6)）、[agent_tools.py:20-26](backend/agent/tools/agent_tools.py#L20) 的 `get_weather` 返回硬编码「天气晴，气温26摄氏度」。两套执行栈并存会让后来者误判入口。

**13. 输入长度**

[chat/base.py:70-71](backend/app/api/chat/base.py#L70-L71) `ChatRequest.message: str = Field(min_length=1)` —— **无 max_length**，只靠 token 预算间接兜底。也无全局请求体大小限制（除知识库上传显式 20MB）。

**14. 管理端知识库无法跨租户**

[knowledge.py:57](backend/app/api/knowledge.py#L57) `admin_household` 返回管理员**自己**的家庭，平台管理员无法为其他家庭管理知识。

**15. 测试工具进了生产目录**

[catalog.py:347-354](backend/agent/platform/catalog.py#L347) 的 `platform.test_high_risk`（high 风险）、`platform.context_probe`、`platform.workflow_confirm` 都在正式目录里，并会被 `sync_builtin_catalog` 写进每个部署的 DB。

**16. 文档漂移**

README 说「0015 迁移链」「490+ 项」，实际 **0016** / **511**。

**17. 依赖与静态检查**

requirements 全部是 `>=x,<y` 区间，无 lock/哈希，无 ruff/mypy/coverage 门禁。CI 只跑 pytest。

---

## 十、建议的处理顺序

如果后续要动后端，建议按这个顺序（每项独立可验证、可回滚）：

1. **认证与审批的安全边界**（问题 #1、#2）—— 影响面最大，改动集中在 `auth.py` + `approvals.py` + 一张 login_attempt 表或 Redis 计数器；登出用 jti denylist（Redis 已在栈内）。审批有效期改服务端上限最省事。
2. **错误契约与全局异常处理**（#3）—— 在 `main.py` 加 `RequestValidationError` handler 把 422 转成 `{"detail":{"code":"VALIDATION_FAILED","fields":[...]}}`，再把 40 处字符串 detail 逐步改成 code。前端 `extractErrorMessage` 已经兼容，无需同步改。
3. **可观测性最小集**（#4）—— `dictConfig` + request id 中间件 + `/health` 探 DB/迁移版本。这三件加起来大约 100 行，收益极高。
4. **家庭成员管理补全**（#5）—— 6 个端点 + 邀请接受路径改成「软删除/可恢复」，并补上被报错信息引用却不存在的转让所有权。
5. **统一家庭角色判定**（#6、#9）—— 抽一个 `require_household_role(min_role)` 依赖，把 5 处分散判定收敛；官方 Agent 查找改用稳定 key。
6. **市场性能与异步化**（#7、#8、#10）—— 搜索下推到 SQL + 分页；`_revectorize_knowledge` 改投 Celery。
7. **清理与文档**（#11–#17）—— 删死 except、下线 V1 栈、补 max_length、测试工具移出生产目录、README 订正、加 ruff + coverage 门禁。

---

## 验证方式

本次是只读评估，未改动任何文件。若要验证本文结论：

```bash
cd backend

# 端点/表的实际数量
grep -rh "@router\.\(get\|post\|put\|patch\|delete\)" --include=*.py app | wc -l   # → 149
grep -rh "__tablename__" --include=*.py app/db | wc -l                            # → 62

# 测试实际可收集数
.venv/bin/python -m pytest --collect-only -q | tail -2                             # → 511 tests collected

# 证实「无防护/无可观测性」的三条 grep（均应为空）
grep -rn "rate_limit\|slowapi\|limiter" --include=*.py app
grep -rn "exception_handler\|BaseHTTPMiddleware\|@app.middleware" --include=*.py app
grep -rn "logging.basicConfig\|dictConfig\|request_id\|prometheus" --include=*.py app

# 错误契约分裂的实际比例
grep -rn 'detail={"code"' --include=*.py app | wc -l                               # → 121
grep -rEn 'detail="' --include=*.py app | grep HTTPException | wc -l               # → 40
```

手动复核建议：`make backend-test`（全绿）+ 以 admin 登录 Swagger `/docs`，依次走一遍 setup 向导 → 家庭日常 → 创作者发布 → 市场安装 → 审核决策，确认功能闭环与本文「完成度」判断一致。
