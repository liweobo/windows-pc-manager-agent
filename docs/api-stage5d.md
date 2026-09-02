# Stage 5D 逐函数 API 文档

本文覆盖 Stage 5D 新增或直接修改的公开函数、方法、校验器及重要内部辅助函数。所有 `FrozenModel`
均为不可变 Pydantic 模型；校验失败抛出 `ValueError`/`ValidationError`，安全与协调层使用各自的显式
异常。本文中的“建议”永远不表示执行授权。

## `config/agents.py`

- `AgentRuntimeLimits.require_consistent_limits()`：模型创建后自动运行。确认委派深度不超过图深度，
  且网页块与文档块上限之和不超过总消息数；防止调用方用互相矛盾的配置绕过预算。

## `domain/agents.py`

- `AgentCapabilityManifest.require_unique_capabilities()`：拒绝重复输入/输出类型、数据分类、工具、委派
  角色或 Memory scope；同时拒绝任何 model-backed Agent 直接写 Memory。
- `AgentCapabilityManifest.canonical_digest()`：将完整清单按键排序并生成 SHA-256，用于运行时身份、
  委派和审计绑定；修改任一能力都会改变摘要。
- `AgentDelegationRequest.require_live_unique_delegation()`：确保过期时间晚于创建时间，且子能力列表
  没有重复项。它只检查模型形状，运行时仍会检查当前时间、父身份和根目标。
- `AgentMessageEnvelope.require_live_message()`：确保消息尚有正生命周期、trust labels 非空且唯一、
  Agent 消息不冒充系统策略，并验证 finding/delegation payload 的 task、node、goal、recipient 一致。

其余模型没有可调用方法：`AgentRuntimeIdentity` 表示运行时身份；`AgentToolProposal` 是未信任 R0 工具
建议；`DelegationBudget` 是递减预算；`AgentFinding` 是引用型发现；`DomainPreparationProposal` 的
`execution_authorized` 类型固定为 `False`；`AgentResult` 不含确认或授权字段。

## `domain/task_graph.py`

- `TaskNode.require_unique_references()`：拒绝节点内重复 input/output reference，避免同一引用产生歧义。
- `TaskDependency.reject_self_dependency()`：拒绝自己依赖自己的边。
- `TaskGraph.require_goal_and_unique_nodes()`：自动核对目标 SHA-256、非空节点、唯一 node ID 和唯一边。
- `TaskGraph.create(goal, nodes, dependencies=())`：从易失目标生成摘要并创建不可变图；调用者无需自己
  计算 digest。
- `TaskGraph.canonical_digest()`：散列可持久化图元数据；目标正文被字段排除，只以摘要参与绑定。

## `domain/context.py`

- `ContextItem.require_labels()`：要求 trust label 和 classification 非空、唯一，并拒绝重复来源引用。
- `ContextPackage.require_budget_compliance()`：再次核对总消息、字符、文档块、网页块、Memory 项和引用
  数量，避免绕开 `ContextGovernanceService` 直接构造超限包。
- `RecentEntityReference.require_ephemeral_lifecycle()`：要求 expiry 晚于 created，并强制
  `requires_fresh_resolution=True`，因此 recent hint 不能成为目标 authority。

## `domain/memory.py`

- `MemoryEntry.require_valid_lifecycle()`：拒绝更新时间早于创建、到期时间不晚于更新，以及 Sensitive/
  Prohibited 值进入持久化模型。
- `MemoryQuery.require_unique_filters()`：要求至少一个且不重复的 scope，category 过滤也不能重复。

## `domain/task_outcomes.py`

- `FactProvenance.precedence`：返回固定证据强度：Domain verified 4、Domain observed 3、user supplied 2、
  model inference 1。
- `TaskOutcome.require_disjoint_nodes()`：拒绝同一 node 同时出现在 completed 与 incomplete 集合。
- `TaskJournalEntry.require_consistent_counts()`：拒绝完成数加阻止数超过节点数、重复 Agent 角色和倒置
  时间戳。

## `safety/agent_capabilities.py`

- `default_agent_manifests()`：返回完整 V1 角色矩阵。只有清单内 R0 read-only 工具可由 Domain Agent
  建议；所有 Agent 的执行和确认能力固定关闭。
- `AgentCapabilityRegistry.__init__()`：创建未封口、空的默认拒绝注册表。
- `AgentCapabilityRegistry.register(manifest)`：封口前新增一个角色；重复或封口后注册会失败。
- `AgentCapabilityRegistry.seal()`：要求 `AgentRole` 全集均已登记，然后冻结注册表。
- `AgentCapabilityRegistry.manifest(role)`：仅在封口后返回精确角色清单；未知或未封口请求失败。
- `AgentCapabilityRegistry.roles`：按角色字符串排序返回当前角色，用于稳定 UI/审计输出。
- `build_agent_capability_registry()`：注册默认全集并封口，是应用组合根使用的工厂。
- `AgentToolAccessPolicy.__init__(capabilities)`：注入已封口 Agent 能力注册表。
- `AgentToolAccessPolicy.validate(identity, proposal, registry)`：核对 identity digest、角色工具 allow-list、
  原 Tool Registry 清单确实 read-only，并用工具 input schema 验证参数。只返回已验证参数，不执行。
- `AgentDelegationPolicy.__init__(capabilities)`：注入能力矩阵。
- `AgentDelegationPolicy.narrow(parent, target_role, requested)`：核对父身份、目标角色是否可委派，并要求
  请求工具是子角色工具集合的精确子集；越权抛出 `CAPABILITY_ESCALATION_REJECTED`。
- `manifest_mapping(registry)`：返回角色到不可变清单对象的普通快照，供 UI/测试读取，不暴露修改 API。

## `safety/task_graph.py`

- `TaskGraphValidator.__init__(limits)`：绑定本地硬预算。
- `TaskGraphValidator.validate(graph)`：校验节点数、边引用、Role/Domain 所有权、DAG、深度，并要求每个
  `EXECUTE_DOMAIN_ACTION` 都有 confirmation-wait 祖先；没有任何执行副作用。
- `TaskGraphValidator._validate_roles(nodes)`：检查 Domain Agent 所有权；只有 Orchestrator 可等待确认，
  只有 Verifier/Orchestrator 可做验证节点。
- `TaskGraphValidator._topological_order(parents, children)`：使用 Kahn 算法返回排序和每个节点深度；
  排序数量小于节点数量即表示有环。
- `TaskGraphValidator._has_ancestor_type(...)`：向上遍历依赖，确认执行节点的任意祖先中存在指定节点类型。

## `safety/task_goal.py`

- `TaskGoalBoundary.require_unique_scope()`：要求至少一个 Domain，并拒绝重复 Domain、capability 或引用。
- `TaskGoalBoundaryPolicy.validate_graph(boundary, graph)`：Task ID/goal digest 必须一致，非 GENERAL Domain
  必须是允许集合的子集。
- `TaskGoalBoundaryPolicy.validate_delegation(boundary, request)`：限制委派的 task、goal、能力和 context
  reference，防止网页/文档指令扩大根目标。
- `TaskGoalBoundaryPolicy.validate_proposal(boundary, proposal)`：工具建议必须属于同一任务且工具名在根
  capability allow-list 中。

## `safety/context.py` 与 `safety/cross_domain.py`

- `required_trust_for_source(source)`：返回每种 Context 来源不可降低的最小 trust label。
- `ContextTaintPolicy.validate_source(item)`：检查来源必需标签、禁止非系统数据声明系统信任、阻止
  Secret/Credential 分类和已知 secret 赋值模式。
- `ContextTaintPolicy.validate_text(value)`：对用户目标或摘要执行同一已知 secret 模式检查。
- `ContextTaintPolicy.derived_labels(inputs, model_generated)`：合并全部输入标签，移除系统信任，并按需
  追加 `MODEL_GENERATED`；用于保证网页/文档污染不会经摘要消失。
- `CrossDomainDataFlowPolicy.decide(classification, destination, external_transmission=False)`：按有限矩阵
  返回 `ALLOW/TASK_SCOPED/REFERENCE_ONLY/BLOCK`。Secret 永远阻止；文档/用户数据外传默认阻止；
  未列出组合默认阻止。

## `context/governance.py`

- `ContextGovernanceService.__init__(capabilities, limits, taint=None, data_flow=None)`：注入清单、预算及可
  测试替换的 taint/跨域策略。
- `ContextGovernanceService.build(...)`：核对 UUID、目标摘要、目标 secret、Agent 身份；合并明确 items
  与最小 Memory，逐项执行来源、角色可读分类和跨域校验；reference-only 项替换为 opaque reference；
  超预算整项省略；返回正文不可序列化的 `ContextPackage`。
- `ContextGovernanceService._memory_items(memory)`：把已由 Memory read policy 筛选的 entry 转成
  `USER_SUPPLIED/USER_DATA` Context，并保留 entry UUID 来源；不提升为系统指令。

## `context/recent_references.py`

- `RecentEntityReferenceStore.__init__(ttl_seconds=600, max_entries=100, now=None)`：建立有界线程安全内存
  存储；非正 TTL/容量被拒绝，`now` 用于确定性测试。
- `remember(conversation_id, kind, owner_domain, identity_digest)`：先清理过期项，满时淘汰最旧项，再
  创建强制 Fresh resolve 的短期 hint。
- `resolve(reference_id, conversation_id)`：清理过期项后只返回同会话当前 hint；未知、过期、跨会话
  均抛出 `RecentReferenceError`。
- `clear()`：应用退出或用户清理时移除全部易失 hint。
- `_prune_locked()`：在调用方已持锁时删除所有到期项，不接触磁盘。

## `safety/memory.py`

- `MemoryWritePolicy.decide(candidate)`：按 sensitivity、secret/越权指令、key-scope-category 矩阵、有限
  值格式、来源、置信度和显式意图，返回 BLOCK、EPHEMERAL_ONLY 或 REQUIRE_USER_CONFIRMATION。
- `MemoryWritePolicy._valid_value(key, value)`：验证枚举值、1 MiB–10^15 字节阈值、1–3650 天、UUID
  目录引用、普通应用名和有限 UI tab code；未知 key 默认 false。
- `MemoryReadPolicy.validate(query)`：要求查询 scope 全部属于角色矩阵，禁止 `load_all_memory` 式访问。
- `readable_scopes(role)`：稳定排序返回角色可读取 scope；Safety/Verifier/Audit 为空。

## `persistence/memory.py`

- `_utc_iso(value)`：把时间统一为 UTC ISO 字符串写入 SQLite。
- `_logical_digest(candidate)`：对 scope/category/key/source_ref 生成逻辑键；value 不决定记录身份。
- `_payload_digest(entry)`：对持久化 entry 生成完整一致性摘要，用于检测数据库篡改/损坏。
- `_to_row(entry, logical_digest)`：把领域对象编码成 SQLAlchemy row，并写入 payload digest。
- `_decode(row)`：从 row 重建 `MemoryEntry` 并核对 digest；不一致抛 `MemoryStoreError`。
- `_purge_expired(session, now)`：在当前事务中物理删除 TTL 到期值，并为每项写入不含 value 的
  `EXPIRED` 元数据事件；返回实际删除数量。
- `MemoryRepository.__init__(database_path)`：为独立 Memory schema 创建 SQLite engine/session factory。
- `initialize()`：创建 `memory_entries/events/settings`，确保默认 enabled 设置存在；数据库错误失败关闭。
- `is_enabled()`：读取当前 Memory 开关；缺失或数据库错误失败关闭。
- `set_enabled(enabled, now)`：事务更新开关并追加不含值的 ENABLED/DISABLED 事件。
- `upsert(candidate, now)`：按 logical digest 创建或版本化更新；计算 TTL，保存完整 entry 和 value-free
  event；事务异常回滚。
- `query(query, now)`：按允许 scope/category、未过期条件和 limit 查询；过期值先物理删除并记事件。
- `list_all(now, limit=500)`：供用户管理页面查看所有未过期 Memory；不受 Agent scope 查询接口替代。
- `delete(memory_id, now)`：物理删除一项 value 并追加 value-free DELETED 事件；不存在返回 false。
- `clear(scope, now)`：物理删除指定 scope 或全部值，并按实际数量追加 CLEAR 事件。
- `close()`：释放 SQLAlchemy engine 连接池。
- `_event_row(event)`：把不含 value 的领域事件编码为数据库 row。

## `memory/service.py`

- `explicit_setting_candidate(key, value)`：把 GUI 明确设置转换为匹配该 key 的 category/scope 候选；
  未支持 key 抛 `MemoryServiceError`，不直接保存。
- `MemoryService.__init__(repository, audit, write_policy=None, read_policy=None, now=None)`：注入存储、审计、
  策略与时钟。
- `enabled`：返回存储中的 Memory 开关。
- `set_enabled(enabled)`：更新开关并写 value-free 审计；关闭不删除值或 Audit。
- `save(candidate, confirmed)`：运行写策略并审计决定；BLOCK/EPHEMERAL 拒绝持久化；需要确认而未确认
  时拒绝；Memory 关闭时拒绝；通过后 upsert 并记录元数据变更。
- `query(query)`：先执行角色 scope 策略；Memory 关闭返回空 context，开启则返回有界查询结果。
- `list_for_user()`：即使 Memory 关闭仍列出用户可管理的未过期项。
- `delete(memory_id, confirmed)`：要求明确确认，物理删除并按实际结果审计。
- `clear_scope(scope, confirmed)`：要求明确确认，清空一个 scope 并记录数量。
- `clear_all(confirmed)`：要求明确确认，清空全部并记录数量；UI 将其作为更高影响动作展示。
- `close()`：关闭独立 Memory repository。

## `agents/base.py`、`domain_agents.py`、`safety_reviewer.py`、`planner.py`

- `AgentIdentityFactory.__init__(capabilities)`：绑定封口清单。
- `AgentIdentityFactory.create(role)`：由本地 runtime 创建 UUID、manifest digest 和 prompt version；不接受
  模型自报身份。
- `AgentIdentityFactory.validate(identity)`：重新计算清单与 Prompt 身份并拒绝过期/伪造对象。
- `DomainPreparationAgent.__init__(identity, domain)`：核对角色确实拥有该 Domain。
- `DomainPreparationAgent.identity`：只读返回运行时身份。
- `DomainPreparationAgent.prepare(node, context)`：核对 Context 与 node 所有权，生成
  `execution_authorized=False` 的 handoff；不会访问 Tool Registry/Executor/Confirmation。
- `SafetyReviewerAgent.__init__(identity, graph_validator, goal_policy)`：只接受 runtime Safety Reviewer 角色。
- `SafetyReviewerAgent.identity`：只读返回审查角色身份。
- `SafetyReviewerAgent.review(graph, boundary)`：运行两个确定性策略并返回 finding；不生成安全批准或确认。
- `PlannerAgent.__init__(validator)`：注入图校验器。
- `PlannerAgent.compile(user_goal, draft)`：把 provider key 替换为本地 UUID；忽略 provider 的角色声明，
  使用 `_runtime_role` 分配所有权；构建后执行完整图校验。
- `PlannerAgent._runtime_role(node_type, domain)`：WAIT 固定 Orchestrator、VERIFY 固定 Verifier、Domain 节点
  使用本地映射、GENERAL understand/summary 固定 Planner/Orchestrator；其余 GENERAL 类型拒绝。

`ProposalAgent.identity` 与 `ProposalAgent.run(context)` 是 provider-neutral Protocol：实现者必须返回
`AgentResult`，但协议没有执行或确认方法。

## `orchestration/agent_selection.py`

- `AgentSelectionPolicy.domains_for_route(route)`：把已验证请求 route 映射到一个支持的 TaskDomain；聊天
  等不可行动 route 拒绝。
- `roles_for_domains(domains)`：拒绝空/重复/未知 Domain；单域只返回 Orchestrator+Domain，多域才增加
  Planner+Verifier。
- `agent_role_for_domain(domain)`：返回本地唯一 Domain owner；GENERAL 等未知映射失败关闭。

## `orchestration/delegation.py`

- `DelegationCoordinator.__init__(identities, policy, goal_policy, limits)`：建立线程安全 single-use issuance、
  consumption 和每根任务计数状态。
- `create(...)`：验证父身份、UUID node、剩余次数/深度、根任务总次数、能力子集和目标边界；创建短期
  请求并绑定签发父实例与清单摘要。
- `consume(parent, request, payload)`：验证父身份、时限、未消费和 issuer binding；创建本地 child 身份，
  从 context references 与父类型推导 trust labels，构造并校验 envelope，然后原子标记已消费。

## `orchestration/resource_locks.py`

- `TaskResourceLockService.__init__()`：创建进程内 lease 表和重入锁。
- `acquire(task_id, node_id, resources)`：拒绝重复 resource，按 digest 排序后原子检查冲突；read/read 允许，
  任一 write 与相同 digest 冲突；成功返回不可变 lease。
- `release(lease)`：只有完整匹配原对象才释放，伪造/修改 lease 拒绝。
- `release_task(task_id)`：取消任务时释放其未来调度 lease；不撤销已经完成的业务动作。

## `agents/verifier.py`

- `TaskOutcomeAggregator.aggregate(task_id, facts, completed, incomplete, cancelled=False, blocked=False)`：每个
  fact code 只保留最高 provenance；按 blocked/cancelled/partial/completed 生成精确状态。模型来源的
  `*_VERIFIED` 强制降为部分完成，不进行多数投票。

## `persistence/task_runtime.py`

- `_encode(entry)`：把 journal 转为 canonical JSON 与 SHA-256，正文不在模型内。
- `_decode(row)`：核对 payload digest、row task/revision 与领域模型；损坏抛 `TaskJournalError`。
- `TaskJournalRepository.__init__(database_path)`：创建独立任务摘要 SQLite engine。
- `initialize()`：建表，并把 CREATED/RUNNING/WAITING_CONFIRMATION 任务原子标记 INTERRUPTED；返回这些
  task UUID，绝不恢复执行。
- `create(entry)`：只允许新 task ID，写入摘要及 digest。
- `get(task_id)`：返回并验证一个 journal；不存在或损坏失败关闭。
- `save(entry, expected_revision)`：compare-and-swap 更新；revision 不连续或并发冲突拒绝。
- `list_recent(limit=100)`：按更新时间降序返回经过完整性检查的有限摘要。
- `close()`：释放连接池。

## `orchestration/task_graph.py`

- `TaskGraphBuilder.build(goal, domains)`：拒绝空/重复 Domain，构造 understand → 每域 prepare → wait →
  execute → verify → summarize 的依赖图；节点仍不携带 authorization。
- `TaskCoordinator.__init__(repository, audit, capabilities)`：绑定持久化摘要、元数据审计、封口角色清单
  和易失 graph 表。
- `create(graph)`：持久化 content-free RUNNING journal，把含目标正文图只留内存，并审计摘要。
- `graph(task_id)`：只返回当前进程内活动图；重启或终态后失败。
- `set_status(...)`：禁止终态再次迁移，以 compare-and-swap 更新状态/计数/revision；进入终态后清除
  易失图并写审计。
- `cancel(task_id)`：保留已完成计数，只将未来协调标为 CANCELLED，不调用 Undo。
- `list_recent(limit=100)`：为 Task Center 返回持久化摘要。
- `active_node_view(task_id)`：创建只读 UI 状态视图：understand 完成、prepare ready、其余 pending；不
  修改图。
- `close()`：清除所有易失目标图并关闭 repository。
- `_audit_entry(entry, event_code)`：把角色字符串恢复为有限 enum，并记录 ID/digest/count/version 元数据。

## `orchestration/agent_runtime.py` 与 `app/agents.py`

- `Stage5DAgentRuntime.__init__(...)`：组合能力、身份、图、目标、Context、Memory 与 resource lock 服务；
  不接收 Domain Executor。
- `prepare_request(request, route)`：沿共享文本/语音 dispatcher 的 route 选择 Domain，再调用
  `prepare_domains`。
- `prepare_domains(user_goal, domains)`：构建最小图/角色/工具边界，经 Safety Reviewer 确定性审查，
  创建 journal；没有工具执行。
- `prepare_domain_handoff(task_id, domain, items=())`：要求同一活动任务、边界和唯一 prepare node；读取
  最小 scope Memory，构造 Context，生成 non-authoritative proposal，并将任务置为等待原 Domain 确认。
- `cancel(task_id)`：释放调度资源和易失 boundary，再取消未来协调。
- `close()`：清除 boundary，关闭 coordinator 和 Memory。
- `AgentServices.__init__(...)`：保存 Stage 5D runtime、delegation、recent references 和重启中断数量。
- `AgentServices.close()`：清除 recent refs 并按顺序关闭 runtime。
- `build_agent_services(settings, audit)`：应用 composition root；初始化独立 Memory/journal schema、处理
  restart interrupt、创建默认能力矩阵和全部策略，不修改既有 Tool Registry。

## 既有组合根与主窗口的 Stage 5D 变更

- `AppSettings.from_environment()`：额外读取 `PC_MANAGER_AGENT_LIMITS` JSON，并用
  `AgentRuntimeLimits` 校验；无变量时使用保守默认值，非法或互相矛盾配置拒绝启动。
- `ApplicationRuntime.__init__(settings)`：在原有 Browser/Office/Domain 服务之外创建一个共享
  `AgentServices`；不替换既有 registry、confirmation 或 executor。
- `ApplicationRuntime.close()`：先关闭 Agent/Memory/task journal，再按原顺序关闭 Browser、Office 与
  其他服务，避免退出后保留易失 goal/reference。
- `MainWindow.__init__(runtime, parent=None)`：增加 Task Center、Memory 页面和当前协调 task ID；其余
  Domain tab 顺序只向后移动，不改变业务组件。
- `MainWindow._register_agent_task(request, route)`：在原业务分流前创建单域 graph 与 handoff，并明确
  检查所有 proposal 的 `execution_authorized=False`；失败时停止进入业务页。
- `MainWindow._cancel_current_surface()`：保留原业务页面取消逻辑，并额外取消当前 Agent task 的未来
  协调；状态更新失败不会伪装成功，也不会调用全局 Undo。

## `audit/memory.py` 与 `audit/multi_agent.py`

- `MemoryAuditLogger.__init__(repository, git_commit=None)`：绑定通用审计库和可选构建 commit。
- `write_decision(candidate_id, key, decision, reason_code)`：记录 candidate ID、key digest、决策和原因；
  明确标记 value 未保存。
- `changed(action, memory_id=None, scope=None, affected_count=0, confirmed=True)`：记录 Memory 元数据变更；
  CLEAR 记 R2，其余本地更改记 R1，开关动作不要求确认。
- `MultiAgentAuditLogger.__init__(repository, git_commit=None)`：绑定 Agent 元数据审计。
- `task_event(...)`：记录 task/graph/goal/manifest digest、状态、节点数、角色和 Prompt 版本，不记录
  goal/context/prompt 正文。
- `capability_denied(task_id, node_id, role, reason_code)`：记录一次默认拒绝边界及固定原因码。

## `providers/llm/agent_base.py` 与 `openai_agents.py`

- `AgentGraphDraft.require_unique_keys()`：拒绝重复 provider node key 和指向未知 key 的依赖。
- `AgentPlanningRequest.require_exact_goal_and_unique_scope()`：核对目标摘要，以及非空唯一 Domain/Role
  allow-list。
- `AgentLLMProvider.name` / `create_task_graph(request)`：供应商无关 Protocol；只能返回结构化草案。
- `_ResponsesAPI.parse(...)`、`_OpenAIClient.responses`：用于依赖注入和 fake 测试的最小 OpenAI Protocol。
- `OpenAIAgentLLMProvider.__init__(model, api_key, client=None)`：要求显式 model/key；默认客户端 30 秒超时、
  最多一次 SDK retry。key 只进入 SDK 初始化，不写请求或日志。
- `name`：稳定返回 `openai`。
- `create_task_graph(request)`：通过 Responses structured parse 请求 `AgentGraphDraft`；仅返回草案、request
  ID 和 Prompt version。API/解析错误转换为不含原始响应的安全异常。该适配器默认未接入 UI。

## `ui/task_center_tab.py`

- `TaskCenterTab.__init__(services, parent=None)`：创建只显示摘要的任务表、刷新和“取消未来工作”按钮；
  不展示 chain-of-thought、Prompt、Context 或确认控件。
- `register_task(prepared)`：记录最近选中的 task ID 并刷新表。
- `refresh()`：从 journal 读取 task ID、状态、节点/完成/阻止数和角色；不读取用户目标正文。
- `cancel_selected()`：取消表格选中任务的未来协调；已完成 Domain action 不回滚。

## `ui/memory_tab.py`

- `MemoryTab.__init__(service, parent=None)`：创建 Memory 开关、有限 key/value 编辑器、表格和显式管理按钮。
- `refresh()`：更新开关、表格和状态文本；即使关闭仍显示可删除的已有数据。
- `_toggle_enabled(enabled)`：切换 Memory 读写；不清除已有项和 Audit。
- `_save()`：把有限设置创建为 candidate，显示具体确认后由 policy/service 保存；错误友好显示。
- `_load_selected()`：把选中行的 key/value 载入编辑器，用于明确更新同一 logical key。
- `_delete()`：列出精确 key/scope 并确认后物理删除单项。
- `_clear_scope()`：确认后只清空当前选中 scope。
- `_clear_all()`：高影响确认后清空全部 Memory value；不清除 Audit。
