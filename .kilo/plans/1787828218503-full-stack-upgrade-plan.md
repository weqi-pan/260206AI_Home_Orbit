# 智能客服系统全栈升级实施计划（已细化）

> 目标：将现有 Streamlit + LangChain 演示项目升级为 FastAPI + Vue3 前后端分离的完整智能客服系统，引入 Deep Agents、认证权限、MySQL、管理后台与客服业务闭环。
> 计划状态：设计已与用户确认，技术风险已核实（deepagents 依赖链、API 形态、向量库改造点），可交付实施。

## 一、已确认决策

| 决策点 | 结论 |
|--------|------|
| 后端 | FastAPI（REST + SSE 流式），删除 Streamlit `app.py` |
| Agent | `deepagents==0.7.9`（子代理 + 文件系统 + 显式 TodoListMiddleware） |
| 数据库 | MySQL 8.x 为主，SQLite 兜底（无 MySQL 自动降级），SQLAlchemy 2.x 统一抽象 |
| 认证 | JWT（access 2h + refresh 7d）+ bcrypt（直用 bcrypt 库，不用 passlib）；`accounts`（凭证/角色）与 `users`（业务档案）双表 |
| 注册 | 开放注册（role=user），种子脚本创建 admin + demo 账号 |
| 前端 | Vue3 + Vite + TypeScript + Element Plus + Pinia + Vue Router |
| 会话 | 多会话管理，LLM 自动生成会话标题；**不使用 LangGraph checkpointer，DB 为会话真相源**（每请求注入历史消息，与现状一致） |
| 管理端 | 用户管理、知识库管理（含 FAQ 审核工作台）、设备/库存管理、工单中心、数据看板 |
| LLM 功能 | 推荐追问、情绪识别、FAQ 自动生成、会话标题（辅助模型 qwen-flash，配置化） |
| 客服闭环 | 工单系统（单条 admin_reply + 状态流转，无消息线程）、库存预警+缺货登记、满意度评价、设备对比 |
| 部署 | docker-compose（mysql+backend+frontend/nginx）+ 本地 venv 双模式 |
| 模型 | 主模型 qwen3-max 不变（ChatTongyi，langchain_community） |
| 端口约定 | 后端 8000 / 前端 dev 5173 / docker nginx 80 |

## 二、关键风险核实结论（2026-08-27 调研）

1. **deepagents 0.7.9 依赖**：`langchain>=1.3.17`（连带 `langgraph>=1.2.11,<1.3`）、`langchain-core>=1.6.0`、`Python>=3.11`。当前项目锁 `langchain==1.2.10/langchain-core==1.2.11/langgraph==1.0.8`，**必须整体升级**，这是阶段 0 的核心工作。
2. **deepagents 0.7 API**（官方文档核实）：
   - `create_deep_agent(model=..., tools=[...], system_prompt=..., subagents=[...], middleware=[...])`；model 支持字符串或模型实例（ChatTongyi 实例是否被接受需 spike 验证，不行则查 provider 字符串写法）
   - **0.7 起规划工具为 opt-in**：需显式加 `langchain.agents.middleware.TodoListMiddleware()` 才有 `write_todos` 工具
   - 子代理通过 `subagents=` 声明（每个有独立 name/description/prompt/tools/middleware），主代理经内置 `task` 工具委派，子代理独立上下文、单次汇报
   - 文件系统是必备脚手架不可移除，默认内存 StateBackend；可用 `FilesystemMiddleware(tools=[...])` 收窄可见工具
   - 中间件走 langchain 标准 agent middleware stack，现有 `wrap_tool_call` 监控模式应可复用（升级后 spike 确认）
3. **向量库现状缺陷**：`rag/vector_store.py` 用 `md5.text` 文件去重、chunk 无 metadata → **无法按文件/FAQ 从 Chroma 删除**，知识库管理功能必须先改造（见第六节）。
4. **本机 Python 版本未验证**（沙箱限制），阶段 0 第一步校验 `python --version >= 3.11`。

## 三、目标仓库结构

```
backend/
├── app/
│   ├── main.py                 # FastAPI 入口：建库/种子/CORS/路由挂载/启动校验API_KEY
│   ├── core/                   # config.py(.env+YAML统一)、security.py(JWT/bcrypt/当前账号依赖/角色依赖)
│   ├── api/                    # auth.py chat.py knowledge.py tickets.py devices.py admin.py
│   ├── schemas/                # Pydantic 请求/响应模型
│   ├── db/                     # models.py session.py repositories.py seed.py(建表+种子+CSV导入)
│   └── services/               # chat_service.py sentiment.py title_gen.py followup.py faq_gen.py
├── agent/
│   ├── deep_agent.py           # create_deep_agent 组装（替代 react_agent.py）
│   ├── subagents/              # presales.py aftersales.py report_ticket.py（提示词+工具集）
│   └── tools/                  # agent_tools.py(改造) providers.py(改造) middleware.py(monitor/log保留)
├── rag/vector_store.py         # 改造：metadata 标记 + 按文件/FAQ 删除 + 重建接口
├── model/factory.py            # 迁移 + get_aux_model()（qwen-flash）
├── prompts/                    # main + 3个子代理提示词 + rag_summary
├── config/                     # 迁移 + 新增 auth/db/aux_model 配置项
├── utils/                      # 迁移
├── data/                       # knowledge/（上传目录）+ 样例CSV；现有4个知识文件迁入 knowledge/
├── tests/                      # pytest
└── requirements.txt
frontend/
├── src/
│   ├── api/  stores/  router/  components/  views/
│   │   views/auth/(Login,Register)  chat/Chat  tickets/MyTickets
│   │   views/devices/(List,Compare)  admin/(Dashboard,Users,Knowledge,FaqReview,DevicesInventory,Tickets)
├── vite.config.ts              # dev 代理 /api → localhost:8000
└── package.json                # vue3 element-plus pinia vue-router axios markdown-it highlight.js
docker-compose.yml              # mysql(healthcheck: mysqladmin ping) + backend + frontend(nginx,多阶段构建)
.env.example
```

## 四、数据库 Schema（SQLAlchemy，兼容 MySQL/SQLite）

约定：UUID 主键用 `String(36)`；时间存 UTC DateTime；MySQL 连接串强制 `charset=utf8mb4`（中文）；不迁移旧 `data/app.db`，首次启动 `create_all` + 种子。

**新增**：
1. `accounts`：id(UUID), username(unique), password_hash, role('admin'|'user'), status('active'|'disabled'), must_change_password(bool), user_id(FK users, 可空——管理员无档案), created_at
2. `chat_sessions`：id(UUID), account_id(FK), title(默认"新对话"), created_at, updated_at
3. `tickets`：id, account_id, session_id(可空), title, summary(LLM摘要), emotion, priority('low'|'normal'|'high'), status('open'|'processing'|'resolved'|'closed'), admin_reply(可空,单条), created_at, resolved_at
4. `knowledge_files`：id(UUID), filename, file_md5, file_type, status('active'|'deleted'), uploaded_by(FK accounts,可空), created_at —— **取代 md5.text**
5. `faq_pairs`：id, question, answer, source_file_id(FK), status('pending'|'approved'|'rejected'), created_at, reviewed_at
6. `restock_notifications`：id, account_id, model_id(FK devices), status('waiting'|'notified'), created_at

**改造**：
7. `conversation_history`：+ session_id(FK chat_sessions), sentiment(可空), sentiment_score(可空), feedback('like'|'dislike',可空)；原字符串 session_id 废弃
8. `inventory`：+ min_stock(int 默认 5，预警阈值)
9. `users`：结构不变；新档案 user_id 用应用层自增数字串（SELECT MAX+1，起始 1002）

**保留**：`devices`、`device_usage_records`。

**种子数据**（启动时幂等）：admin 账号（`ADMIN_INITIAL_PASSWORD` env，默认 Admin@123，must_change_password=true）；demo 账号（demo/Demo@1234）关联档案 user_id=1001（含样例使用记录，保证报告演示开箱可用）；现有 4 个知识文件 bootstrap 入 knowledge_files 表。

## 五、认证设计细节

- JWT payload：`sub=account_id, role, type('access'|'refresh'), exp`；secret 来自 env `JWT_SECRET`（未设置用开发默认值并打警告日志）
- 注册流程：username 唯一校验 → bcrypt(cost=12) → 创建 account + 自动创建 users 档案（FK 关联）→ 直接返回 token
- `must_change_password`：前端登录后检查标记强制跳改密页；后端仅返回标记不做硬拦截（演示级简化）
- 鉴权依赖：`get_current_account`（解析 Bearer）→ `require_admin`（role 校验，403）；资源归属校验：session/ticket/notification 的 account_id 必须匹配当前用户（403/404）
- 刷新：POST /auth/refresh 校验 type=refresh 后签发新 access；无黑名单，登出由前端清除（范围外注明）
- axios 拦截器：401 → 自动 refresh → 重放原请求 → 仍失败则跳登录页

## 六、Agent 改造（deepagents）

组装方式（`agent/deep_agent.py`）：
```python
agent = create_deep_agent(
    model=get_chat_model(),              # ChatTongyi(qwen3-max)；spike 确认实例直传可行
    tools=[get_weather, get_current_month],
    system_prompt=load_main_prompt(),
    subagents=[presales_subagent, aftersales_subagent, report_ticket_subagent],
    middleware=[TodoListMiddleware(), monitor_tool, log_before_model],
)
```

| 子代理 | 提示词文件 | 工具 |
|--------|-----------|------|
| 售前设备顾问 presales | prompts/presales_prompt.txt | search_devices、query_device_info、query_inventory、compare_devices(新,Markdown表格返回) |
| 售后知识专家 aftersales | prompts/aftersales_prompt.txt | rag_summarize、search_faq(新,查已审核faq_pairs) |
| 报告与工单 report_ticket | prompts/report_prompt.txt(改造自原report_prompt) | fetch_external_data、register_restock_notify(新)、create_ticket(新) |

**关键变更**：
- 移除 `fill_context_for_report` 工具与 `report_prompt_switch` 动态提示词中间件（报告职责由子代理承担）；保留 `monitor_tool`（wrap_tool_call）与 `log_before_model`（before_model）
- **身份注入**：删除 get_user_id/get_user_location 工具；chat_service 从 JWT 构造 `context={"account_id","user_id","city"}`，工具经 `runtime.context` 读取（providers.py 默认值仅兜底）
- TodoListMiddleware 显式启用：报告生成场景前端可展示规划进度（可选增强）
- 文件系统保持默认内存后端；报告子代理可用 write_file 组装报告草稿（可选，不强制）
- 流式：`agent.stream(input, stream_mode=["messages","custom"], context=...)`；monitor_tool 通过 emit_custom_event 发布工具开始/结束事件 → SSE `tool` 事件（若 custom 事件集成受阻，降级为仅 token 流，不阻塞交付）

## 七、SSE 协议规范

`POST /api/chat/sessions/:id/chat`（Bearer 认证，`Accept: text/event-stream`，用 sse-starlette EventSourceResponse，15s 心跳）：

| 事件 | data 结构 | 时机 |
|------|-----------|------|
| `token` | {content} | agent token 级输出（stream_mode="messages"） |
| `tool` | {name, status} | monitor_tool emit_custom_event（尽力而为） |
| `sentiment` | {label, score} | 与 agent 执行**并行**后台线程算用户消息情绪，3s 超时放弃（默认 neutral 不阻塞） |
| `followup` | {questions:[3条]} | agent 完成后、关流前生成 |
| `done` | {message_id} | 流结束 |
| `error` | {message} | 异常兜底 |

- 会话标题：仅首轮对话触发，流关闭后异步生成（fire-and-forget），前端在流结束后刷新会话列表获取（不做 SSE 推送）
- 同步生成器放 StreamingResponse 自动线程池即可；nginx 对 `/api/chat` 设 `proxy_buffering off` + `X-Accel-Buffering: no`

## 八、向量库改造（知识库管理前置条件）

现状问题：md5.text 去重 + chunk 无 metadata → 无法按文件删除。
改造 `rag/vector_store.py`：
1. 所有 chunk 添加 metadata：`{source_file_id, source_type:'kb'|'faq', faq_id?, filename}`（现有 add_documents 改为带 metadata 添加）
2. 去重依据从 md5.text 改为 knowledge_files.file_md5
3. 删除文件：`collection.delete(where={"source_file_id": id})` + 软删表记录；重建：清空 collection 后按 knowledge_files(active) 重新加载
4. FAQ 审核通过：以 `Q:...\nA:...` 文档带 metadata(source_type='faq', faq_id) 入库；FAQ 拒绝/删除：`collection.delete(where={"faq_id": id})`
5. 启动 bootstrap：扫描 `data/knowledge/`（现有 4 个知识文件迁入该目录），不存在的文件同步剔除向量库
6. 上传约束：.pdf/.txt，≤20MB，python-multipart 接收，MD5 重复拒绝

## 九、LLM 辅助服务（app/services/）

| 服务 | 模型 | 说明 |
|------|------|------|
| sentiment.py | qwen-flash + with_structured_output | label(positive/neutral/negative)+score(0-1)，写 conversation_history |
| title_gen.py | qwen-flash | 首轮后生成 ≤12 字标题，更新 chat_sessions |
| followup.py | qwen-flash | 生成 3 条追问，SSE followup 事件 |
| faq_gen.py | qwen3-max | 取文件分块 → 生成 Q&A 对 → faq_pairs(pending)；审核通过才入 Chroma |

辅助模型名 `aux_model_name` 在 config/rag.yml 配置化；全部服务对 LLM 调用失败做静默降级（不影响主流程）。

## 十、API 清单（前缀 /api）

- **/auth**：POST register、POST login（返回 token+must_change_password）、POST refresh、GET me、PUT password
- **/chat**：GET/POST/PATCH/DELETE sessions、GET sessions/:id/messages、POST sessions/:id/chat(SSE)、POST messages/:id/feedback
- **/tickets**（用户）：GET /my、POST /（可带 session_id，后端 LLM 摘要）、GET /my/:id
- **/devices**（用户）：GET /?keyword=、GET /:model_id、POST /compare(model_ids[])、POST /restock-notify/:model_id
- **/knowledge**（admin）：GET/POST/DELETE files、POST rebuild、POST files/:id/generate-faq、GET faq?status=、PUT faq/:id/review
- **/admin**（admin）：GET dashboard、GET/PUT accounts（禁用/启用/角色/重置密码）、CRUD devices、PUT inventory（含 min_stock）、GET restock-notifications（可标记 notified）、GET/PUT tickets（处理/回复/关闭）

**Dashboard 指标**：今日会话数、近7日消息数、满意度率=like/(like+dislike)、工单各状态计数、低库存设备列表（总库存≤min_stock）、知识文件/FAQ 数量。

## 十一、前端要点

- 路由守卫：未登录→/login；/admin/* 校验 role（auth store 持久化 pinia+localStorage）
- 聊天页：fetch+ReadableStream 消费 SSE（EventSource 不支持 Authorization 头）；markdown-it+highlight.js 渲染；会话侧栏；消息卡片（情绪标签/点赞点踩/追问按钮点击即发送）
- 管理端：Element Plus 表格表单；Dashboard 统计卡片（ECharts 可选增强，首版用 EP 卡片）
- vite dev 代理 /api→8000；后端 CORS 允许 http://localhost:5173

## 十二、依赖变更清单（backend/requirements.txt）

```
# 升级（锁定）
langchain>=1.3.17          # 1.2.10 → 升级（deepagents 强制）
langchain-core>=1.6.0      # 1.2.11 → 升级
langgraph>=1.2.11,<1.3     # 1.0.8 → 升级（langchain 连带要求）
langchain-community>=0.4   # ChatTongyi/DashScopeEmbeddings，升到兼容 core 1.6 的版本
langchain-chroma           # 升到兼容版本
langchain-text-splitters   # 升到兼容版本
# 新增
deepagents==0.7.9
fastapi、uvicorn[standard]、sse-starlette
sqlalchemy>=2.0、pymysql、cryptography      # MySQL（caching_sha2_password 需要 cryptography）
bcrypt（直用，弃 passlib——已停止维护且与 bcrypt4.x 不兼容）、pyjwt
python-dotenv、python-multipart
pytest、httpx                                # 测试
# 保留
dashscope、pypdf、PyYAML、openai(dashscope传递依赖)
# 移除
streamlit
```

## 十三、实施任务顺序

**阶段 0：环境与依赖升级（前置，高风险先做）**
1. 校验/准备 Python ≥3.11；建 backend/ 虚拟环境；按上表升级+安装依赖
2. 冒烟测试：现有代码（middleware 导入 `langchain.agents.middleware`、`langgraph.runtime`、`langchain.tools.tool_node`，ChatTongyi/DashScopeEmbeddings 导入，Chroma 初始化）在新版本下可运行；API 变更则适配
3. deepagents spike（临时脚本，不入库）：`create_deep_agent(model=ChatTongyi实例, tools=[一个现有工具], subagents=[1个], middleware=[TodoListMiddleware(), wrap_tool_call监控])` 跑通一次真实调用；确认 model 实例直传与 custom event 可行性，失败则记录替代方案（provider 字符串 / 降级无 tool 事件）

**阶段 1：后端骨架 + 数据层 + 认证**
4. 建 backend/ 结构，迁移 agent/rag/model/prompts/config/utils（修 import），FastAPI 骨架 + .env/YAML 配置统一
5. SQLAlchemy 模型（第四节全部表）+ MySQL/SQLite 双方言（启动探测 MySQL 失败→SQLite 降级警告）+ 种子数据（admin/demo/设备/库存/CSV）
6. /auth 全部接口 + JWT/bcrypt + get_current_account/require_admin 依赖
7. pytest：注册/登录/refresh/403 权限/资源归属/SQLite 兜底（LLM 全 mock）

**阶段 2：Deep Agent + 聊天接口**
8. 重构 agent 模块（第六节：主代理+3子代理+身份注入+移除旧 hack）
9. 向量库改造（第八节：metadata/删除/重建/bootstrap）
10. chat_service + SSE（第七节协议）+ 多会话 CRUD + 消息持久化 + 情绪/追问并行集成
11. 验证脚本：知识问答/设备/报告/工单四条链路各跑通

**阶段 3：前端用户端**
12. Vite+Vue3+TS 脚手架、路由/状态/axios(401自动refresh)、登录注册页
13. 聊天页（SSE 渲染/多会话/反馈/情绪/追问）、我的工单、设备列表/对比/缺货登记
14. 联调：注册→登录→多轮→报告→工单全链路

**阶段 4：LLM 增值 + 客服闭环**
15. title/followup/sentiment 服务上线（阶段2已建服务则本阶段联调打磨+降级测试）
16. 工单全流程（用户建→管理处理回复→关闭）+ 满意度统计
17. 库存 min_stock 预警列表 + 缺货登记管理端处理

**阶段 5：管理端页面**
18. Dashboard（第十节指标）、用户管理、设备/库存管理
19. 知识库管理：上传/删除(Chroma同步)/重建、FAQ 生成+审核工作台
20. 工单处理中心

**阶段 6：部署与文档**
21. docker-compose（mysql healthcheck→backend depends_on condition:service_healthy、nginx 多阶段构建、proxy_buffering off）、.env.example、/health 端点
22. README 重写；删除根目录 app.py 与 Streamlit 依赖；旧 react_agent.py 清理

## 十四、验证方案

- **自动化（pytest，LLM mock）**：认证全流程、权限 403、资源归属、repositories、双方言降级、SSE 事件序列（mock agent 流）
- **手工验收场景**：
  1. 无 MySQL 环境启动 → SQLite 降级 + 建表 + admin/demo 种子
  2. admin 首登强制改密；普通用户访问 /admin 接口与页面 → 403/重定向
  3. demo 登录→新建会话→"大户型适合什么机器人"（子代理+RAG）→ 标题自动生成→点赞
  4. "给我生成使用报告" → 报告子代理输出 Markdown 报告（demo 关联 1001 样例数据）
  5. "R1-Pro 有货吗" → 缺货 → 登记到货通知 → 管理端可见
  6. 负面情绪消息 → 前端展示情绪标签 → 建议创建工单 → 管理端处理 → 用户端可见回复
  7. 管理端上传 txt → 生成 FAQ → 审核通过 → 用户问答命中 FAQ；删除知识文件后其内容不再被检索
  8. docker-compose up 一键启动全链路可用
- **阶段 0 专项**：升级后旧代码冒烟 + deepagents spike 通过才进入阶段 1

## 十五、风险与缓解

| 风险 | 缓解 |
|------|------|
| langchain 1.2→1.3 升级破坏性变更（middleware 导入路径、create_agent 变动） | 阶段 0 冒烟先行；deepagents 本就要求 1.3.17+，早升早暴露 |
| ChatTongyi 实例不被 create_deep_agent 接受 | spike 验证；备选：provider 字符串 / init_chat_model 适配 |
| custom event 工具状态推送集成受阻 | 降级方案：SSE 只发 token，工具状态不入 UI |
| 本机 Python <3.11 | 阶段 0 安装 3.11+/conda；requirements 注明 requires_python>=3.11 |
| MySQL 不可用 | 自动降级 SQLite（启动探测+日志警告），功能完全一致 |
| SSE 经 nginx 被缓冲 | proxy_buffering off + X-Accel-Buffering 头，docker 验收场景覆盖 |
| LLM 辅助服务拖慢主流程 | 全部并行/异步+超时降级，失败静默不阻塞聊天 |
| DASHSCOPE_API_KEY 缺失 | 启动即校验并清晰报错（保留现有 get_api_key 逻辑） |

## 十六、范围外（明确不做）

- 管理端全量会话审计检索（用户未选）
- 邮件/短信通知（缺货通知仅站内状态流转）
- 真实天气 API（保留模拟 Provider）
- Alembic 迁移体系（create_all 幂等建表足够，列可选）
- OAuth 第三方登录、验证码、JWT 黑名单/登出失效
- 工单消息线程（单条 admin_reply）
- 旧 SQLite 数据迁移（fresh start + CSV 种子）
