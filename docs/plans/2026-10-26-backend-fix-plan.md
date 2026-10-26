# 后端修复计划（按 PR 分阶段）

> 依据：[2026-10-26-backend-assessment.md](2026-10-26-backend-assessment.md) 的评估结论。
> 风格与 [2026-10-23-frontend-optimization-plan.md](2026-10-23-frontend-optimization-plan.md) 保持一致：每阶段一个分支 + 一个 PR，独立可验证、可回滚。

## Context

评估的结论是：业务功能面基本齐全（20 个 router / 149 个端点 / 62 张表 / 26 个内置工具 / 511 项测试），模型治理与审计做得扎实；欠账集中在**平台工程层**（认证防护、可观测性、错误契约、家庭成员管理）和若干同步阻塞点。

本计划覆盖 **高危 2 项 + 中危 9 项**，加上评估中新发现的一项（前端完全没有审批 UI），共 **11 项**，拆成 6 个 PR。低危 7 项（死代码、V1 栈、输入长度、跨租户知识库、测试工具入生产目录、README 漂移、ruff/coverage 门禁）本次**只列清单不排期**，见文末。

**硬约束**

- 测试 URL、API Key 只放在本地 `.env` 或 CI secrets 中，不写入代码、示例文件或数据库迁移。
- `frontend/dist/`、`backend/data/` 已在 `.gitignore`，构建与运行产物不得入库。

**已实测确认的前提**（每条都在本机核对过代码）

- 迁移 head = `20260828_0016`；[session.py:13](backend/app/db/session.py#L13) 的 `REQUIRED_DATABASE_REVISION` 是**精确字符串相等**门禁，生产/预览启动不等即 `RuntimeError`；测试模式走 `create_all()` 绕过。
- **6 个测试文件 / 10 处断言**硬编码 `"20260828_0016"`：`test_market_migration.py:37,70`、`test_ops_audit_migration.py:48,77`、`test_creator_migration.py:30,61`、`test_household_daily_migration.py:37,70`、`test_platform_foundation.py:336`、`test_official_device_fix_round1.py:424`。每新增一个 revision 这 10 处都要改 —— 因此本计划**只引入 2 个迁移**。
- 测试全跑 SQLite（`backend/tests/conftest.py` 每用例独立 tmp 库 + `app_mode="test"`，`PRAGMA foreign_keys=ON` 见 [session.py:42](backend/app/db/session.py#L42)），**任何新能力都不得依赖 Redis**。
- [test_acceptance_security.py:41](backend/tests/test_acceptance_security.py#L41) 逐表扫描明文密钥，新增表不得存可读的用户名/凭据原文。
- `create_app`（[main.py:52](backend/app/main.py#L52)）目前**没有任何 exception handler、没有 middleware、没有 logging 配置**；`/health` 返回静态 `{"status":"ok"}`。
- [docker-compose.yml:62](docker-compose.yml#L62) 的启动命令已是 `alembic upgrade head && uvicorn ...`，迁移与 revision 门禁必须同 commit。

---

## 分阶段实施（每阶段一个分支 + PR）

按**依赖顺序**排：PR 1/2 无迁移、纯加法，先把错误契约和日志铺好；PR 3/5 各带一个迁移；PR 4/6 依赖已落地的契约。

### PR 1：错误契约统一 + 全局异常处理

**分支**：`fix/backend-error-contract`　**对应问题**：#3

161 处 `HTTPException` 里 121 处用 `detail={"code": ...}`，**40 处用纯中文字符串**（`knowledge.py` 8、`households.py` 6、`auth.py` 6、`admin/accounts.py` 4、`devices.py` 3、`admin/operations.py` 3、`admin/devices.py` 3、`core/security.py` 2、`tickets.py` 2、`chat/messages.py` 2、`chat/base.py` 1）；Pydantic 校验失败的 422 是 FastAPI 默认数组形态，直接绕过 code 契约。

1. 新建 `backend/app/core/errors.py`：
   - `install_exception_handlers(app)`：注册 `RequestValidationError` handler → **保留 422**，body 为 `{"detail": {"code": "VALIDATION_FAILED", "message": "请求参数校验失败", "fields": [{"path": ".".join(map(str, e["loc"])), "message": e["msg"]}]}}`（只搬 `loc`/`msg`，不搬 `ctx` —— Pydantic v2 会在里面塞原始异常对象，序列化会炸）；注册 `Exception` 兜底 → `logger.exception` + `500 {"detail": {"code": "INTERNAL_ERROR"}}`（不泄露 traceback）。
   - 再注册 `StarletteHTTPException` handler 兜底**纯字符串 detail**：转成 `{"code": 由状态码推导, "message": 原文}`。这层是保险丝 —— Starlette 自己的 404/405 也是字符串 detail，不包的话形状仍会破。领域语义仍由各端点自己给 `detail={"code": ...}`。
   - **不引入 `AppError` 类**：40 处只需换 detail 字面量，多一个没人实例化的异常子类只会增加一层间接。若后续端点需要复用同一错误，再抽。
2. `create_app` 里紧跟 `FastAPI(...)` 构造调用它。
3. 40 处字符串 detail 逐个换成稳定 code，**中文文案不能丢**：写成 `detail={"code": "...", "message": "原来的中文"}`。命名对齐已有风格（`HOUSEHOLD_NOT_FOUND` / `AUTH_INVALID_CREDENTIALS` / `KNOWLEDGE_FILE_NOT_FOUND` / `DEVICE_NOT_FOUND` / `TICKET_NOT_FOUND` / `ADMIN_REQUIRED` …）。`auth.py` 的 6 处与 PR 3 重叠，**本 PR 只加 code、不动语义**。
4. 前端一处小改：[http.ts:13-29](frontend/src/api/http.ts#L13-L29) 的 `extractErrorMessage` 现在优先返回 `detail.code`；改成 `detail` 是对象时**先取 `fields` 里的逐字段原因、再取 `message`、最后退回 `code`**，否则用户会看到 `HOUSEHOLD_NOT_FOUND` 或笼统的「请求参数校验失败」而不是具体中文。该函数有 3 个调用点（[useRequest.ts:21](frontend/src/composables/useRequest.ts#L21)、[chat.ts:173](frontend/src/api/chat.ts#L173)、[http.ts:81](frontend/src/api/http.ts#L81)）。

**验收**

- `grep -rEn 'detail="' --include=*.py backend/app | grep -c HTTPException` → `0`（仅剩 `home/orbit.py` 的节点描述与 `tasks/executor.py` 的记录字段，都不是异常 detail）
- 17 处既有 `status_code == 422` 断言全绿（已确认无一断言 detail 形状）；新增 `test_error_contract.py` 6 项断言契约形状
- `test_auth.py`（14 项）、`test_acceptance_matrix.py` 全绿
- `npm run lint && npm run format:check && npm test -- --run` 绿

---

### PR 2：可观测性最小集

**分支**：`fix/backend-observability`　**对应问题**：#4

无 `logging.basicConfig`/`dictConfig`、无 request id、无 `/metrics`、无耗时；`/health` 不探 DB、不探迁移版本，而 [docker-compose.yml](docker-compose.yml) 每 10s 拿它当健康检查 → 静态探针恒绿。

1. 新建 `backend/app/core/logging.py`：`configure_logging(settings)` 用 `dictConfig` 输出单行 JSON（`ts`/`level`/`logger`/`msg` + 所有 `extra` 字段，异常走 `exc`）；`app_mode == "test"` 时**整体不做配置**（见下方偏差 3）。在 `create_app` 最开始调用。
2. 新建 `backend/app/core/middleware.py`：
   - `RequestContextMiddleware`：生成/透传 `X-Request-ID`、写 `contextvars.ContextVar`、回写响应头、结束打一条 access 日志（method/path/status/duration_ms/request_id）。
   - **SSE 安全**：见下方偏差 1 —— 改用**纯 ASGI 中间件**，只包装 `send`，不做响应缓冲，流式响应天然不受影响。[test_agent_platform_chat_integration.py](backend/tests/test_agent_platform_chat_integration.py)、[test_chat_business.py](backend/tests/test_chat_business.py) 的流式断言是回归闸门。
3. `/health` 带探针但**保留 `status` 键**（[test_auth.py:190](backend/tests/test_auth.py#L190)、[test_knowledge_bootstrap.py:118](backend/tests/test_knowledge_bootstrap.py#L118) 断言 `["status"] == "ok"`）：
   - `status`: `ok` / `degraded`；`checks`: `database`（`SELECT 1`）、`revision`（测试模式 `null`）、`redis`（**仅 production** 才 `PING`，失败记 degraded 而非 500）。
   - 从 [session.py](backend/app/db/session.py) 的 `require_database_revision` 抽出只读的 `current_database_revision(engine)` 复用。
4. `/metrics`（Prometheus 文本）：`backend/requirements.txt` 加 `prometheus-client>=0.21,<1`。两个指标：`http_requests_total{method,path,status}`、`http_request_duration_seconds{method,path}`（histogram）；`path` 用路由模板 `request.scope["route"].path` 避免高基数，未匹配路由记 `__unmatched__`。见下方偏差 4（没有 `app_info`）。

**实现偏差**（已按实际实现回填）

1. **中间件用纯 ASGI，不是 `BaseHTTPMiddleware`。** 后者会额外包一层任务组并缓冲响应体，对 `EventSourceResponse` 有已知断流风险。纯 ASGI 只包装 `send`，流式响应完全不受影响 —— 于是原计划里「对 SSE 关掉计时」这个特例**不需要存在**，也就没有「部分请求不记账」的盲区。
2. **`/health` 与 `/metrics` 落在新文件 [observability.py](backend/app/api/observability.py) 的 router 里，不塞进 `main.py`。** `main.py` 已经在 `create_app` 里堆了 20 个 `include_router`，探针逻辑（含 Redis 探活、降级判定）混进去会把工厂函数撑成杂物间；独立 router 也让 `test_observability.py` 能单独覆盖。
3. **测试模式刻意不做任何 logging 配置。** 原计划是「降到 WARNING + 人类可读格式」，但 pytest 自己接管 root logger，`caplog` 又依赖 propagate：在这里改 handler 会把既有断言弄坏（[test_knowledge_bootstrap.py:101-103](backend/tests/test_knowledge_bootstrap.py#L101-L103) 就在 `caplog.at_level("WARNING")` 里读 `caplog.records`）。所以 `app_mode == "test"` 直接 `return`，全新用例断言「root handler 列表未变」。
4. **没有 `app_info{revision}` 指标。** 迁移版本已经在 `/health` 的 `checks.revision` 里，Prometheus 侧再由 exporter 采集属于重复投射；而 revision 在进程生命周期内不变，做成 Gauge 只会多一个需要主动维护的常量。留待真接监控时按需再加。
5. **`X-Request-ID` 只回写响应头，不进错误 body。** 错误体已是 `{code, message, fields}` 契约，body 里多一个字段会让前端和既有断言都得跟着改；HTTP 客户端读头即可完成关联。
6. **`Settings.log_level` 与 [.env.example](.env.example) 的 `LOG_LEVEL` 注释同步加入**（只写注释与默认值，不含任何真实凭据）。

**验收**

- `docker compose up -d --build` → `curl localhost:8000/health` 有 `status` + `checks`
- `curl localhost:8000/metrics | head` 有指标；`/api/devices/{model_id}` 的标签是路由模板，不是真实 id
- 一次 `POST /api/auth/login` 的 access 日志与异常日志能按 `request_id` 串起来；请求头带 `X-Request-ID` 时原样回显
- `pytest -q` 全绿（528 项：基线 511 + 错误契约 6 + 可观测性 11）；流式用例是回归闸门

---

### PR 3：认证与会话安全（迁移 `0017`）

**分支**：`fix/backend-auth-hardening`　**对应问题**：#1

登录/注册/刷新**无限流、无失败锁定、无失败审计**；**无登出、无 jti denylist、无 refresh 轮换**（[auth.py:22-24](backend/app/api/auth.py#L22-L24) 每次签发同结构令牌）；改密后旧令牌仍有效（`get_current_account` 只查 `status`）；register 对已存在用户名 409 构成账号枚举（[auth.py:33](backend/app/api/auth.py#L33)）。

**迁移** `backend/alembic/versions/20260828_0017_auth_session_hardening.py`（`down_revision = "20260828_0016"`，全文 `op.batch_alter_table`，照抄 [20260828_0016_notification_read_created_at.py](backend/alembic/versions/20260828_0016_notification_read_created_at.py) 的写法以兼容 SQLite）：

- `accounts` 加 `token_version INTEGER NOT NULL server_default="0"`。
- 新表 `refresh_sessions`：`id` PK、`token_id`(unique，= refresh JWT 的 `jti`)、`family_id`(index)、`account_id`(FK CASCADE, index)、`expires_at`、`used_at`、`revoked_at`、`replaced_by_token_id`、`client_ip`、`user_agent`、`created_at`。
- 新表 `login_attempts`：`id` 自增 PK、`scope`(`login|register|refresh`)、`key_hash`(index)、`succeeded`、`created_at`，复合索引 `(key_hash, created_at)`。
- 模型：新建 `backend/app/db/auth_models.py`，在 [models.py:222](backend/app/db/models.py#L222) 尾部按既有「Staged V2 compatibility exports」分段风格 re-export；`Account` 加 `token_version`。
- [session.py:13](backend/app/db/session.py#L13) → `"20260828_0017"`；同步那 10 处测试断言。

**代码**

1. [core/security.py](backend/app/core/security.py)：`create_token` 增加 `jti`、`ver`（`account.token_version`）与可选 `fid`；`get_current_account` 在 `status != "active"` 之后加 `payload.get("ver", 0) != account.token_version → 401`（**缺省 0**，兼容升级前签发的在途令牌）。新增 `verify_dummy_password(password, rounds)` —— 模块级缓存的假 bcrypt 哈希，用于登录失败路径对齐耗时。
   - 注意 [test_acceptance_matrix.py:434](backend/tests/test_acceptance_matrix.py#L434)、[test_acceptance_matrix.py:472](backend/tests/test_acceptance_matrix.py#L472) 直接调 `create_token(account, secret, "access", timedelta)` —— 保持现有位置参数签名，新参数一律 keyword-only 且有默认值。
2. 新建 `backend/app/services/auth_sessions.py`：`issue_session` / `rotate_refresh` / `revoke_refresh` / `revoke_family` / `revoke_all_for_account` / `bump_token_version`。`rotate_refresh` 的关键是**重放即断族**：令牌轮换用带条件的 `UPDATE ... WHERE id=? AND used_at IS NULL`，`rowcount != 1` 或命中 `used_at IS NOT NULL` → `revoke_family` + `bump_token_version` + 401 `{"code":"REFRESH_REUSED"}`（并发双标签页安全）。
3. 新建 `backend/app/core/rate_limit.py`：`RateLimiter` Protocol + `DatabaseRateLimiter`（默认，SQLite 测试可用）+ `MemoryRateLimiter`；`client_ip(request, settings)`。检查点放在 `verify_password` **之前**。`key_hash = sha256(f"{scope}:{username|ip}")`，**不落用户名明文**（满足 [test_acceptance_security.py](backend/tests/test_acceptance_security.py) 的逐表明文扫描）。超限返回 `429 detail={"code":"LOGIN_RATE_LIMITED"}` + `Retry-After`。
4. [api/auth.py](backend/app/api/auth.py)：`_tokens` → `issue_session`；`/refresh` 走 `rotate_refresh` 并返回含新 refresh 的完整 `TokenPair`；新增 `POST /logout`（body `{refresh_token}`，恒 204）与 `POST /logout-all`（认证，204）；register 409 → `{"code":"USERNAME_UNAVAILABLE","message":"无法完成注册，请更换用户名或稍后重试"}`；登录失败分支补 `verify_dummy_password`；`PUT /password` 成功后 `bump_token_version` + `revoke_all_for_account`。
5. [api/admin/accounts.py:33](backend/app/api/admin/accounts.py#L33) `reset_password` 与禁用账号同样 `bump_token_version` + 回收会话。
6. [core/config.py](backend/app/core/config.py) 新增：`auth_rate_limit_enabled=True`、`auth_rate_limit_backend: Literal["database","memory"]="database"`、`login_rate_limit_window_seconds=300`、`login_rate_limit_username_attempts=10`、`login_rate_limit_ip_attempts=30`、`register_rate_limit_ip_attempts=10`、`auth_rate_limit_trust_forwarded_for=False`。`access_token_minutes` 120 → 60（无测试依赖）。同步根目录 [.env.example](.env.example)（**只加注释形式的说明，不写任何真实值**）。
7. `create_app` 的 lifespan 挂 `app.state.rate_limiter`。
8. **前端（必须同 PR，否则会强制登出循环）**
   - [http.ts:31-43](frontend/src/api/http.ts#L31-L43) `refreshAccessToken`：把轮换后的 refresh 一起写回，改调 `auth.setTokens(access, refresh)`。不改的话第二次静默刷新会重放已用令牌 → 断族 → 强制登出。
   - [stores/auth.ts](frontend/src/stores/auth.ts)：新增 `setTokens(accessToken, refreshToken)`（复用 `persist()`）。
   - [api/auth.ts](frontend/src/api/auth.ts)：新增 `logout(refreshToken)` / `logoutAll()`。
   - [AppLayout.vue:19](frontend/src/components/AppLayout.vue#L19)、[AdminLayout.vue:32](frontend/src/components/admin/AdminLayout.vue#L32)：先 best-effort `await logout(auth.refreshToken)`（失败忽略）再本地清除。
   - [ChangePassword.vue:37-53](frontend/src/views/ChangePassword.vue#L37-L53)：改密成功后改为 `auth.logout()` + 跳 `/login` + 提示「密码已修改，请重新登录」（旧令牌已失效，原逻辑会白烧一次 refresh 再被拒）。

**实现偏差**（已按实际实现回填）

1. **`create_token` 无条件签发 `jti`（uuid 兜底），不只是 refresh。** 原计划只让 refresh 带 `jti`，但 `iat`/`exp` 是**秒级精度**：同一秒内签发的两个 access 令牌逐字节相同，`test_refresh_rotates_and_returns_a_new_refresh_token` 就是因此挂掉的（轮换后的 access 与旧的完全相等）。顺带把「每个令牌都有稳定 id」变成后续做访问令牌吊销的抓手。
2. **`auth_sessions` 的函数名与计划不同**：计划写 `issue_session` / `rotate_refresh` / `revoke_refresh`，实际是 `record_session` / `consume_refresh` / `revoke_token`。`consume_refresh` 返回 `"ok" | "reused" | "revoked" | "unknown"` 四态串而不是布尔 —— `"unknown"`（库里没有记录）与 `"revoked"`（族已吊销）要分别处理：前者拒绝但**不**断族（升级前签发的老令牌会落到这里），后者直接拒绝。
3. **限流器拆成 `count` / `record` / `clear` 三个显式方法，没有合并的 `hit()`。** 合并会把「读计数」与「写计数」绑在一起，检查点就必须写在自增之后 —— 阈值会整体偏移一格，第 1 次请求就可能被拦。现在是先 `count` 判断、再 `record`。
4. **登录成功只清用户名维度，不清 IP 维度。** 清 IP 的话，攻击者用自己账号成功登录一次就能把自己那档 IP 计数重置掉，IP 限流等于可绕过。
5. **注册按「每一次请求」计数，不只失败。** 要挡的是批量注册，成功本身就是要挡的对象（原计划写的「只对失败计数」对 register 不适用）。`.env.example` 的注释已同步说明。
6. **`token_version` 只在 `active → disabled` 这个跃迁上 +1**，不是每次写 disabled 都加。管理员重复提交同一条禁用操作不该产生一串审计噪声。
7. **配置名是 `auth_rate_limit_window_seconds`**（计划里的 `login_rate_limit_window_seconds` 改了名）：窗口对 login/register 共用，叫 `login_` 会误导。
8. **`access_token_minutes` 由 120 降到 60**，配合 `token_version` 让吊销延迟上限从 2 小时压到 1 小时。
9. **前端 `setPasswordChanged` 一并删掉**：改密成功后直接登出跳 `/login`，那个标记位不再有消费者，留着只会让人误以为还存在「改密后无感续用」的路径。

**验收**

- `test_auth.py` 14 项全绿（`test_duplicate_registration_is_rejected` 只断言 409，`test_login_rejects_wrong_password` 只断言 401，`test_refresh_and_me` 只读 `access_token`，均不受影响）
- 新增 [test_auth_sessions.py](backend/tests/test_auth_sessions.py) 13 项：`test_logout_revokes_refresh` / `test_logout_all_bumps_token_version` / `test_refresh_replay_revokes_the_whole_family` / `test_login_rate_limited_returns_429_with_retry_after` / `test_admin_reset_password_invalidates_sessions` / `test_rate_limiter_stores_no_plaintext_identifier` / `test_tokens_issued_before_token_version_still_work` / `test_duplicate_registration_message_does_not_confirm_the_username` 等
- 新增 [test_auth_migration.py](backend/tests/test_auth_migration.py) 3 项：`test_auth_hardening_revision_roundtrips`（upgrade → 断言新表新列 → downgrade 到 `0016` → 再 upgrade）、`test_required_revision_matches_auth_hardening_head`、`test_create_all_matches_migration_shape`
- `pytest -q` 全绿：**544 项**（PR 2 后的基线 528 + 认证会话 13 + 迁移 3）
- `npm test -- --run`（26 个文件 105 项）全绿，含 [http.test.ts](frontend/src/api/http.test.ts) 新增的「`refreshAccessToken` 的令牌轮换」两条
- 手工：登录 → 等 access 过期 → 静默刷新 → 继续操作，确认无强制登出

---

### PR 4：审批有效期服务端约束 + 审批 UI

**分支**：`fix/approval-window-and-ui`　**对应问题**：#2 + 新发现项

**问题 A**：[agent_platform.py:22](backend/app/schemas/agent_platform.py#L22) `ApprovalDecisionRequest.expires_at` 由客户端决定（必填），[approvals.py:48-53](backend/app/api/approvals.py#L48-L53) 只校验「在未来」—— 可以批一个十年窗口，绕过「高风险需逐次确认」。

**问题 B（新发现）**：**审批事件到了浏览器但前端丢弃了**。[stream.py:131](backend/app/api/chat/stream.py#L131) 把 runner 事件原样转发，`approval_required` / `approval_resumed`（[compiler.py:192,230,243](backend/agent/platform/compiler.py#L192)、[runner.py:146](backend/agent/platform/runner.py#L146)）确实发到浏览器；但 [chat.ts:66-93](frontend/src/api/chat.ts#L66-L93) 的 `dispatchFrame` 只有 `token/tool/sentiment/followup/done/error` 六个分支，`Chat.vue` 也没有任何 approval 处理 → **高风险审批流程在 UI 上完全不存在**。

**后端**

1. [core/config.py](backend/app/core/config.py) 加 `approval_max_minutes: int = 10`、`approval_default_minutes: int = 5`。
2. `ApprovalDecisionRequest.expires_at` 改为**可选**；`approve()` 改为 `effective = min(客户端值 or utcnow()+default, utcnow()+max)`。过去时间仍 422 `APPROVAL_EXPIRED`。
   - 为什么不是纯服务端：现有 3 个测试都传 5–10 分钟（[test_agent_platform_api.py:53](backend/tests/test_agent_platform_api.py#L53)、[test_acceptance_matrix.py:475](backend/tests/test_acceptance_matrix.py#L475)、[test_agent_platform_fix_round1_api.py:68](backend/tests/test_agent_platform_fix_round1_api.py#L68)），clamp 让它们**零改动**通过，同时把上限锁死。
3. `GET /api/approvals` 补 UI 需要的字段：`tool_key`、`tool_display_name`、`risk_level`（join `tool_definitions`）、`arguments`（`arguments_json` 写入时已被 [audit.py:9](backend/agent/platform/audit.py#L9) 的 `redact()` 处理过，直接 `json.loads`）、`deployment_id`、`session_id`、`run_id`。
   - **注意**：`approval_requests` 表本身没有 `session_id` 列，要从 `agent_runs.session_id` 取（[platform_models.py:311](backend/app/db/platform_models.py#L311)）。避免前端再拼一次 `/api/tools`。

**前端**

4. [api/chat.ts](frontend/src/api/chat.ts)：
   - `ChatStreamCallbacks` 加 `onApprovalRequired?` / `onApprovalResumed?`；`dispatchFrame` 补两个 case（`approval_required` 的 payload 是 `{"approval_id", "code":"APPROVAL_REQUIRED"}`）。
   - 新增 `resumeApproval(approvalId, callbacks, signal)`：`POST /api/approvals/{id}/resume`，**用 fetch + `Accept: text/event-stream`**（该端点返回 `EventSourceResponse`，不能用 axios），复用同文件已有的 `readStream` / `parseFrame`。
   - 新增 `listPendingApprovals()` / `approveApproval(id, expiresAt?)` / `rejectApproval(id)`。
5. [views/Chat.vue](frontend/src/views/Chat.vue)：在助手气泡里渲染审批卡（工具显示名 + 风险等级 + 脱敏参数 + 倒计时），动作为批准 / 拒绝 / 忽略；批准后调 `resumeApproval`，把后续 token 追加到**同一个气泡**。
   - 无障碍：审批卡 `role="region"` + `aria-label`；倒计时不进 `aria-live`；结果通过已有的 `streamAnnouncement`（`role="status" aria-live="polite"`）播报一次。触控目标 ≥44px。
   - 沿用 [styles/global.css](frontend/src/styles/global.css) 的骨架类；`acceptance.test.ts` 断言 admin 与 `Discover/MyAgents/Creator/Household` 源码不含 `orbit-outer` / `orbit-stage`，Chat 目前不在名单里，但不要引入这两个类。
6. [AppLayout.vue](frontend/src/components/AppLayout.vue) 加「待审批」角标（`listPendingApprovals()` 轮询 30s）。可选；没有入口的话用户只能在聊天过程中撞见审批。

**实现偏差**（已按实际实现回填）

1. **第 6 项（AppLayout 待审批角标）没有做，留到后续。** 角标本身没有去处：点开只能跳回聊天页，而审批卡本来就出现在产生它的那一轮回复下面，那才是唯一能判断「该不该批」的上下文。真要做成入口，得先有一个「审批中心」页面，否则只是多一个会闪红点却没有动作的图标。
2. **新增 `onRun` 回调，是这次必需的一条。** 审批后 `POST /approvals/{id}/resume` 的流**以 `run` 帧收尾，没有 `done`**（[runner.py:143-148](backend/agent/platform/runner.py#L143-L148)）—— 只监听 `onDone` 的话，最后一个未完结段落的播报和「回复完成」永远不会触发，读屏用户听到的是「已批准，继续执行」之后就没下文了。同理，首轮聊天在等待审批时也是 `run{status:"waiting_approval"}` 收尾，`onRun` 对两条路径都适用：`status === "completed"` 才播报完成，`waiting_approval` 不播报（否则会抢在审批卡出现之前喊「回复完成」）。
3. **`resumeApproval` 复用新抽出的 `streamRequest`**，不是照着 `streamChat` 再写一遍。两者只差 URL 与 body（resume 不发 `message`），401 静默刷新、非 2xx 的 `extractErrorMessage`、`readStream` 全都要一模一样；复制一份等于把刷新逻辑维护两份。
4. **审批卡做成独立组件 + `MessageItem` 的 `approvals` 插槽**，而不是把模板直接铺在 `Chat.vue` 里。`Chat.vue` 已经 500+ 行，卡片还带自己的倒计时定时器（要 `onUnmounted` 清）；挂在气泡的具名插槽里既保证它跟着这条消息走，也避免和 `tool-events` 那块并排区块抢版面。
5. **新增 `parseServerTime`（[utils/format.ts](frontend/src/utils/format.ts)）**，这是计划里没预见到的一个坑：后端 `utcnow()` 是 `datetime.now(timezone.utc).replace(tzinfo=None)`，序列化出来是 `2026-10-26T10:35:00` **不带 Z**；按 ECMAScript 规范，`Date.parse` 对「date-time 无偏移」按**本地时间**解释，在 UTC+8 的机器上倒计时会凭空多出 8 小时（10 分钟的窗口显示成 8 小时 10 分）。倒计时一律走这个函数，`formatDateTime` 展示历史时间不受影响，保持原样。
6. **`ChatApproval` 带 `state` 状态机**（`pending | busy | approved | rejected | failed`）而不是一个布尔。`busy` 要同时压住按钮和显示「处理中」；`failed` 要能原地重试（不重新拉卡片）；`approved` 之后按钮组整体换成「收起」——用布尔拼不出这四态。
7. **`GET /api/approvals` 的 `arguments` 解析容忍坏数据**：`arguments_json` 为空串、非 JSON 或不是对象时退回 `{}`（[approvals.py](backend/app/api/approvals.py) `_redacted_arguments`）。历史行是可能存在的，一条脏数据不该让整个待审批列表 500。
8. **`resolveApproval` 在列表里查不到时退回占位卡片**（工具名/参数缺失、`risk_level` 记为 `high`），而不是不显示。approve/reject 端点只需要 `approval_id`，让用户点得到比让界面看起来「没反应」要好。

**验收**

- 后端新增 [test_approval_window.py](backend/tests/test_approval_window.py) 6 项：`test_approval_window_is_capped_server_side`（传 `now + 3650 days` → 返回 `expires_at` 被夹到 `now + approval_max_minutes`）、`test_approval_window_can_be_shortened_by_the_client`（用户要求更短时服务端**不反向拉长**）、`test_approval_without_expires_at_uses_the_server_default`、`test_approval_in_the_past_is_still_rejected`（422 `APPROVAL_EXPIRED`）、`test_pending_approvals_carry_what_the_ui_needs`、`test_pending_approvals_tolerate_unparsable_arguments`
- `test_agent_platform_fix_round2/3/4` 的 resume 用例全绿（3 处原有 `expires_at` 传值**零改动**：客户端值都落在 `now + 5~10min`，早于服务端上限，`min` 取客户端值）
- 前端 [Chat.test.ts](frontend/src/views/Chat.test.ts) 新增 3 项：`approval_required` → 卡片出现（工具名/风险/脱敏参数）→ 点批准 → 调 `resumeApproval` → 后续 token 落在**同一个气泡**、`run{completed}` 播报完成；倒计时用 approve 响应里的 `expires_at`；拒绝不触发 resume
- [chat.test.ts](frontend/src/api/chat.test.ts) 新增 3 项：`approval_required` 不再落到 default 被丢弃、没有 `approval_id` 的帧不上抛、`approval_resumed` 上报
- 新增 [format.test.ts](frontend/src/utils/format.test.ts) 3 项：无时区时间戳按 UTC 解析、带 `Z`/`+08:00` 的不再补、空值与非法值返回 `null`
- `pytest -q` 全绿：**550 项**（PR 3 后 544 + 审批窗口 6）
- `npm test -- --run` 全绿：**27 个文件 114 项**（PR 3 后 26/105 + chat 3 + Chat 3 + format 3）
- 手工：给某个 Agent 绑上 `platform.test_high_risk` 并触发，确认聊天页出现审批卡、批准后回复继续、返回的 `expires_at` 不超过 10 分钟

---

### PR 5：家庭成员管理 + 统一角色判定（迁移 `0018`）

**分支**：`fix/household-lifecycle`　**对应问题**：#5 + #6 + #9

三个问题合并（它们共享同一片代码，拆开会产生三次重复迁移）：

- **家庭生命周期缺失**：[households.py](backend/app/api/households.py) 只有 4 个端点（`/current`、`/{id}`、`POST /{id}/invitations`、`POST /invitations/{id}/accept`），**没有**成员列表/改角色/移出/转让/退出/撤销邀请/查看待处理。[households.py:72-98](backend/app/api/households.py#L72-L98) 的 `accept_invitation` 会**硬删**原 membership，原家庭变空时连家庭一起删；报错「请先转移原家庭所有权」指向的操作根本不存在。
- **角色判定不一致**：[market/service.py:247,427,493](backend/app/market/service.py#L247) 要求 owner/admin，[installations.py:32](backend/app/api/installations.py#L32) 的 `POST ""` 只查 membership → viewer 可通过。同类判定散落 5–8 个文件。
- **官方 Agent 用中文显示名定位**：[chat/base.py:88](backend/app/api/chat/base.py#L88)、[chat/sessions.py:23](backend/app/api/chat/sessions.py#L23)、[seed.py:169](backend/app/db/seed.py#L169) 都硬编码 `"设备管家"`；`MetadataSchema`（[versions.py:76-77](backend/agent/platform/versions.py#L76-L77)）只有 `description`，没有稳定 key 可复用。

**迁移** `backend/alembic/versions/20260828_0018_household_lifecycle.py`（`down_revision = "20260828_0017"`）：

- `invitations` 加 `revoked_at`、`revoked_by_account_id`（FK accounts）。
- `agent_definitions` 加 `official_key: String(64) NULL` + `UniqueConstraint("household_id","official_key")`（MySQL/SQLite 都允许多个 NULL）。模型沿 `tenant_parent_constraints()` 追加。
- **FK 修正**（不改的话删 membership 会被外键挡住，`PRAGMA foreign_keys=ON` 下测试会直接报错）：
  - `agent_runs.member_id`（[platform_models.py:310](backend/app/db/platform_models.py#L310)）→ nullable + `ondelete="SET NULL"`，迁移前先 `UPDATE agent_runs SET member_id=NULL WHERE member_id NOT IN (SELECT id FROM household_members)`。
  - `restock_notifications`、`notification_reads` 的复合 FK → `(household_members.account_id, household_members.household_id)` 改为 `ondelete="CASCADE"`（都是按人存的候补/已读标记，成员离开后无意义）。
  - `tickets` 的同类复合 FK → **改为只保留 `household_id → households(CASCADE)` 与 `account_id → accounts`**，删掉指向 household_members 的那条。理由：工单是家庭的支持历史，成员离开后应保留；原来的复合 FK 只是「插入时必须是成员」的约束，这个保证在 ticket 创建路径里已有 membership 检查。
  - 模型文件同步改，否则 `test_*_migration.py` 的 create_all ↔ 迁移结构比对会挂。
- [session.py:13](backend/app/db/session.py#L13) → `"20260828_0018"`；同步那 10 处断言。

**代码**

1. 新建 `backend/app/core/household_access.py`：`HOUSEHOLD_ALL/ADMINS/WRITERS` 常量 + `membership_of` / `require_membership` / `require_role` / `require_household_role(*roles, forbidden_code=...)`（返回 FastAPI 依赖）。**不得 import router**。
2. [api/households.py](backend/app/api/households.py) 新增（全部走 `detail={"code":...}`；**字面量路由必须排在 `/{household_id}` 之前**，否则会被路径参数吞掉）：
   - `PATCH /{id}/members/{account_id}`（owner 可改任意；admin 仅可改 member/viewer）
   - `DELETE /{id}/members/{account_id}`（同上；不能删 owner；自己删自己必须走 `/leave`）
   - `POST /{id}/transfer-ownership`（仅 owner；目标须已是本家庭成员；原子交换：目标 → owner，原 owner → admin）
   - `POST /{id}/leave`（body `{confirm_delete_household: bool = False}`）
   - `GET /{id}/invitations`（owner/admin）、`DELETE /{id}/invitations/{invitation_id}`（置 `revoked` + `revoked_at`）
   - `GET /invitations/received`（任何账号）
   - 成员列表**不新增端点**：`GET /households/current` 已返回 `members`，只需给 `MemberResponse` 加 `joined_at`、`is_self`。
3. **`accept_invitation` 重构为非破坏性**：默认 409 `HOUSEHOLD_LEAVE_REQUIRED`，只有 body 带 `confirm_leave=true` 才走与 `POST /leave` 共用的内部 `leave_household()`。接受用条件 UPDATE `WHERE status='pending'`，`rowcount != 1` → 409（并发双接受安全）；插 membership 捕获 `IntegrityError` → 409。
   - **为什么不用软删除/墓碑行**：`uq_household_members_account` 是普通唯一索引，MySQL 8 不支持带 `WHERE` 的部分索引（要生成列），会让 `create_all` 与 Alembic 链分叉；且大量 `select(HouseholdMember).where(account_id==...).scalar()` 会把墓碑读成「当前家庭」。显式 leave 是唯一不污染查询的方案。
4. **离开后必须补一个独居家庭**（关键，否则账号会被卡死）：注册即建独居家庭（[auth.py:45-48](backend/app/api/auth.py#L45-L48)），而 `exactly_one_household`（[chat/base.py:101](backend/app/api/chat/base.py#L101)）在「零个家庭」时抛 `409 HOUSEHOLD_MEMBERSHIP_MISSING`，系统里没有别的建家庭入口。所以 `leave_household()` 在最后一步为离开者新建独居家庭 + owner membership（名字沿用 `f"{username}的家庭"`）；`DELETE /members/{id}` 同理。
5. 收敛角色判定：[api/installations.py](backend/app/api/installations.py) 删掉 `_membership`/`_manager`，改用 `Depends(require_household_role("owner","admin", forbidden_code="INSTALLATION_PERMISSION_FORBIDDEN"))`；`POST ""` 的 household 在 body 里，用 `require_role(...)` 内联，**以 owner/admin 为准**（[test_acceptance_matrix.py:403-408](backend/tests/test_acceptance_matrix.py#L403-L408) 已断言 market install 对 member/viewer 是 403；全仓库没有测试或前端调用 `POST /api/installations`，只有 PUT/DELETE permissions 子路由在用）。[market/service.py](backend/app/market/service.py) 三处换成 `HOUSEHOLD_ADMINS` 常量，**保留 `role="owner"` 默认参数**（[test_acceptance_concurrency.py:146](backend/tests/test_acceptance_concurrency.py#L146) 直接调 `install(...)` 不传 role）。[creator/base.py:31-32](backend/app/api/creator/base.py#L31-L32) 的 `WRITER_ROLES`/`PUBLISH_ROLES` 改成同组常量的别名。
6. **`official_key`**：[seed.py:152](backend/app/db/seed.py#L152) 的 `seed_official_device_agent` 与 [seed.py:268](backend/app/db/seed.py#L268) 的 `_seed_official_agent` 写入 `official_key`（`device_butler` / `household_chef` / `household_planner` / `household_encyclopedia` / `household_steward`），并对**已存在的 agent 幂等回填**（`elif agent.official_key != key: agent.official_key = key`）—— 不回填的话老库升级后仍全是 NULL，查找照样失效。查找改为 `AgentDefinition.official_key == "device_butler"`。[test_official_household_agents.py](backend/tests/test_official_household_agents.py) 断言的是显示名，不受影响。
7. 变更动作写审计：[service.py:75](backend/app/audit/service.py#L75) 的 `audit_admin_action` 已带 `sanitize_detail`，直接复用（`household.member.role_update` / `.remove` / `household.owner.transfer` / `household.member.leave` / `household.invitation.revoke`）。
8. **前端** [Household.vue](frontend/src/views/Household.vue) + [api/platform.ts](frontend/src/api/platform.ts)：
   - `platform.ts`：`HouseholdMember` 加 `joined_at` / `is_self`；新增 `updateMemberRole` / `removeMember` / `transferOwnership` / `leaveHousehold` / `fetchHouseholdInvitations` / `revokeInvitation` / `fetchReceivedInvitations` / `acceptInvitation`。`fetchHousehold` / `inviteMember` 签名不变。
   - `Household.vue`：成员行按 `household.role` + `member.role` 显示角色下拉、移出、转让所有权、退出家庭；新增「待处理邀请」（owner/admin，可撤销）与「收到的邀请」（所有人，可接受）两个面板。
   - 确认弹窗复用 [MyAgents.vue](frontend/src/views/MyAgents.vue) / [Discover.vue](frontend/src/views/Discover.vue) 的 `modal-backdrop` + `role="dialog"` + [useModalFocus.ts](frontend/src/composables/useModalFocus.ts) 模式（仓库**不用** `ElMessageBox`，测试靠 `[data-testid]`）。收到 409 `HOUSEHOLD_LEAVE_REQUIRED` 时弹「退出当前家庭并加入」，确认后带 `confirm_leave=true` 重试。
   - 所有变更后调 `useRequest` 的 `load()` 重取。

**验收**

- [test_platform_foundation.py:61](backend/tests/test_platform_foundation.py#L61) 与 [test_acceptance_matrix.py:356](backend/tests/test_acceptance_matrix.py#L356) 的接受请求要补 `{"confirm_leave": true}`（注册即建独居家庭，任何邀请接受都会先命中 leave 门）
- 新增 `test_household_members.py`（角色矩阵 + 跨家庭 404 + owner 不可退出/被删 + 唯一成员退出需确认 + 转让原子性 + 邀请撤销幂等 + 离开后自动获得独居家庭）
- 新增 `test_household_lifecycle_migration.py`（upgrade → downgrade → upgrade 三遍，含 FK 行为断言）
- [Household.test.ts](frontend/src/views/Household.test.ts) 的 `vi.mock('../api/platform')` 工厂要补新导出
- 手工：两账号互相邀请 → 接受时出现「退出当前家庭并加入」确认 → 成功后原家庭被回收、新家庭列表正确；owner 转让后角色交换；唯一成员退出需二次确认且退出后仍有独居家庭

---

### PR 6：市场性能与分页

**分支**：`fix/market-perf-and-pagination`　**对应问题**：#7 + #8 + #10

安装在请求线程内同步跑向量化（[market/service.py:406-408](backend/app/market/service.py#L406-L408) 的 `executor.submit(...)` 紧跟 `executor.run(key)`，生产也一样）；搜索是全量 Python 过滤（[service.py:46-57](backend/app/market/service.py#L46-L57) 先查全部 published 再 `if query in row.name`）；chat sessions/messages、`/home/*` 列表、`/tickets/my`、`/market/my`、`/market/favorites` 都没有 limit/offset。

1. **搜索下推 SQL + 分页**：`_published_entries` 改 `or_(MarketEntry.name.like(...), MarketEntry.description.like(...))`，对 `%`/`_` 转义；加 `page`/`page_size`。
   - 把 `clamp_page` 与 `MAX_PAGE_SIZE`（[audit/service.py:20,92](backend/app/audit/service.py#L20)）**移到共享模块** `backend/app/core/pagination.py`，`audit`/`ops`/`market`/`chat` 统一 import（避免 chat 反向依赖 audit）。
   - 响应形状：`/market/listings` 现在是 `{"official": [...], "entries": [...]}`（[market/types.ts:37](frontend/src/market/types.ts#L37)）。**不破坏形状**：`entries` 保持数组，另加平级 `entries_total` / `page` / `page_size`；前端 [market/api.ts:15](frontend/src/market/api.ts#L15) 与 `Discover.vue` 加「加载更多」。`official` 只有 5 条，不分页。
2. **向量化异步化**：照抄仓库已有的 `app_mode` 分派模式（[creator/trials.py:20-31](backend/app/api/creator/trials.py#L20-L31)、[reviews.py:177-184](backend/app/api/reviews.py#L177-L184)）：
   - `_revectorize_knowledge` 保留 jobs 计算（纯 DB 读）在请求线程；`app_mode != "production"` → 现有 `executor.submit` + `executor.run`（测试与预览仍同步、可断言）；production → 新增的 `worker_tasks.dispatch_market_revectorize(...)` 一次性交给 broker。
   - 新增 [worker_tasks.py](backend/app/tasks/worker_tasks.py) 的 `market_revectorize_run` / `dispatch_market_revectorize`（照 [worker_tasks.py:80](backend/app/tasks/worker_tasks.py#L80) `dispatch_sandbox_trial` 的写法），并在 [worker_tasks.py:113](backend/app/tasks/worker_tasks.py#L113) `register_tasks` 注册 `tasks.market-revectorize-run`。
   - 目标：生产安装接口耗时不再等于向量化耗时；[test_market_install_upgrade.py:274](backend/tests/test_market_install_upgrade.py#L274) 走 test 同步路径保持全绿。
3. **分页**：
   - `GET /chat/sessions/{id}/messages` 做**真分页**（最容易无限增长，且前端 `listMessages` 现在一次拉全部，硬截断会静默丢历史）：加 `limit`（默认 100、上限 200）+ `before_id` 游标，响应仍是数组；[chat.ts:56](frontend/src/api/chat.ts#L56) 的 `listMessages(sessionId, beforeId?)` 加参数，`Chat.vue` 滚到顶部时加载更早消息。
   - 其余列表（`/chat/sessions`、`/home/*` 六个列表、`/tickets/my`、`/market/my`、`/market/favorites`）加**硬上限**（默认 500，`?limit=` ≤500），**响应形状不变** → 零前端改动，先把最坏情况挡住；超上限时响应头带 `X-Result-Truncated: 1`，不静默截断。

**验收**

- 新增 `test_market_search_pagination.py`（`%` 转义、page/page_size clamp、`entries_total` 与 `entries` 长度一致）
- `test_market_install_upgrade.py` 全绿
- `Chat.test.ts` 加历史加载用例
- `pytest -q` 511 项基线不下滑
- 手工：`/market/listings?query=厨房&page=2&page_size=20` 返回 `entries_total`；生产模式下安装带知识资产的市场条目，接口在向量化完成前返回，Celery 日志出现 `tasks.market-revectorize-run`

---

## 本次不排期（低危）

只在评估文档里记录，本次不动：

| 问题 | 位置 | 备注 |
|---|---|---|
| #11 死代码 `except PermanentTaskError` | [admin/operations.py:106](backend/app/api/admin/operations.py#L106) | [executor.py:118](backend/app/tasks/executor.py#L118) 内部已 catch |
| #12 遗留 V1 Agent 栈 | `agent/deep_agent.py`、`agent/tools/agent_tools.py` | `get_weather` 返回硬编码天气 |
| #13 `ChatRequest.message` 无 `max_length` | [chat/base.py:71](backend/app/api/chat/base.py#L71) | 只靠 token 预算间接兜底 |
| #14 管理端知识库无法跨租户 | [knowledge.py:57](backend/app/api/knowledge.py#L57) | admin 被限制在自己家庭 |
| #15 测试工具进生产目录 | [catalog.py:347-354](backend/agent/platform/catalog.py#L347-L354) | 改 `enabled=False` 而非删除，`test_creator_sandbox.py` 等用例依赖它们 |
| #16 README 漂移 | `README.md:49,50,95` | 「0015」「490+」实际是 0016 / 511 |
| #17 依赖与静态检查 | `backend/requirements.txt`、CI | 无 lock/哈希、无 ruff/mypy/coverage |

---

## 风险与回滚

| 风险 | 处置 |
|---|---|
| 迁移与 revision 门禁不同步导致生产启动失败 | 迁移文件与 `REQUIRED_DATABASE_REVISION` 必须在**同一 commit**；回滚时 `alembic downgrade -1` 与旧镜像一起退 |
| SQLite batch 重建 FK 时丢数据 | 迁移前先清理孤儿 `agent_runs.member_id`；`test_household_lifecycle_migration.py` 在 tmp SQLite 上跑 upgrade→downgrade→upgrade 三遍 |
| PR 3 的 refresh 轮换未同步前端 → 强制登出循环 | 前端改动**必须同 PR**；手工走「锁屏 2 小时 → 回来继续操作」，确认只静默刷新一次 |
| 限流误伤正常用户 | 阈值（用户名 10/5min、IP 30/5min）远高于测试的最大失败次数（单文件用例最多 2 次）；`auth_rate_limit_enabled` 可一键关 |
| 中间件破坏 SSE 长连接 | 用纯 ASGI 中间件（只包 `send`，不缓冲响应体），不用 `BaseHTTPMiddleware`；两条流式测试是回归闸门 |
| 成员离开后账号卡在「零家庭」 | `leave_household()` 必须补建独居家庭；验收里专门有一条用例覆盖 |
| 删 membership 误删工单历史 | `tickets` 改为只挂 household 与 account，不再挂 household_members；已写进迁移与模型 |
| 错误契约改动让前端显示 code 而非中文 | `detail` 同时保留 `message`，并让 `extractErrorMessage` 优先取 `message` |

---

## 关键文件清单

| 文件 | 改动 |
|---|---|
| [core/{errors,logging,middleware,rate_limit,household_access,pagination}.py](backend/app/core/) · [api/observability.py](backend/app/api/observability.py) | 新增 |
| `backend/app/db/auth_models.py` · `backend/app/services/auth_sessions.py` | 新增：`RefreshSession`、`LoginAttempt`、签发/轮换/吊销 |
| [auth.py](backend/app/api/auth.py) · [security.py](backend/app/core/security.py) · [schemas/auth.py](backend/app/schemas/auth.py) · [admin/accounts.py](backend/app/api/admin/accounts.py) | token_version、轮换、登出、限流、枚举、假哈希 |
| [approvals.py](backend/app/api/approvals.py) · [agent_platform.py](backend/app/schemas/agent_platform.py) | 有效期夹取、列表补字段 |
| [households.py](backend/app/api/households.py) · [schemas/households.py](backend/app/schemas/households.py) · [household_models.py](backend/app/db/household_models.py) | 6 个新端点、非破坏性接受、补建独居家庭 |
| [installations.py](backend/app/api/installations.py) · [market/service.py](backend/app/market/service.py) · [creator/base.py](backend/app/api/creator/base.py) | 统一角色判定 |
| [chat/base.py](backend/app/api/chat/base.py) · [chat/sessions.py](backend/app/api/chat/sessions.py) · [seed.py](backend/app/db/seed.py) · [platform_models.py](backend/app/db/platform_models.py) · [models.py](backend/app/db/models.py) | `official_key`、FK 修正 |
| [main.py](backend/app/main.py) · [knowledge.py](backend/app/api/knowledge.py) · [devices.py](backend/app/api/devices.py) · [tickets.py](backend/app/api/tickets.py) · `api/admin/*.py` · [chat/messages.py](backend/app/api/chat/messages.py) | 异常处理、中间件、40 处字符串 detail |
| [worker_tasks.py](backend/app/tasks/worker_tasks.py) | `tasks.market-revectorize-run` |
| `backend/alembic/versions/20260828_0017_*.py` · `0018_*.py` · [session.py](backend/app/db/session.py) · [requirements.txt](backend/requirements.txt) | 两个迁移、revision 门禁、prometheus-client |
| `backend/tests/`（6 文件 10 处 revision 断言 + 2 处接受用例 + 3 处 `expires_at`） | 同步更新 |
| [http.ts](frontend/src/api/http.ts) · [auth.ts](frontend/src/stores/auth.ts) · `frontend/src/api/{auth,chat,platform}.ts` · [Chat.vue](frontend/src/views/Chat.vue) · [Household.vue](frontend/src/views/Household.vue) · [ChangePassword.vue](frontend/src/views/ChangePassword.vue) · [AppLayout.vue](frontend/src/components/AppLayout.vue) · [AdminLayout.vue](frontend/src/components/admin/AdminLayout.vue) · `frontend/src/market/{api,types}.ts` | 轮换持久化、审批 UI、成员管理 UI、退出登录、加载更多 |

---

## 验证方式（每个 PR 都要跑）

```bash
cd backend && .venv/bin/python -m pytest -q          # 基线 511 项，只增不减
cd frontend && npm test -- --run && npm run lint && npm run format:check && npm run build && npm run size
```

PR 级额外检查见各阶段「验收」小节。
