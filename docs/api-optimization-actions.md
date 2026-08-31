# Stage 4E3 API：逐函数说明

所有路径均位于 `src/pc_manager_agent`。公开工具只接收 UUID 引用。以下 `bind`、结果接收和 GUI
事件是应用内部接口，不在 LLM 工具 Schema 中，也不能签发确认。构造器只注入依赖，除特别标注外
不扫描文件、不联网、不修改系统。异常必须向上报告，不得触发 fallback 或 retry。

## 模型与路由策略

| 函数 | 作用、输入输出与失败语义 |
|---|---|
| `OptimizationCapability.domain` | 将有限复查能力映射到唯一业务所有者；无通用特权目的地。 |
| `OptimizationActionRoute.check_destination` | Pydantic 后置校验能力与领域一致；NONE 不可声称 ROUTABLE。 |
| `OptimizationBenefitObservation.check_measurement` | measured 必须同时有前后有限非负数；归因不可 HIGH/MEDIUM，禁止声称由本动作导致。 |
| `OptimizationActionOutcome.check_evidence` | 已执行结果必须具备原业务 plan/transaction/confirmation/risk；verified 必须匹配状态，V1 禁止 BENEFIT_OBSERVED。 |
| `OptimizationSession.check_items` | 检查建议唯一、结果属于对应建议、同清单最多一个 ROUTED。 |
| `OptimizationReceiptKind.domain` | 将有限持久化事务机制映射到业务领域，不返回 executable 或工具对象。 |
| `OptimizationRoutingError.__init__` | 保存稳定 reason_code，并构造拒绝异常；不记录外部正文。 |
| `OptimizationActionPolicy.evaluate` | 检查报告中候选/发现引用唯一且存在，检查结构化类型和保护分类；返回可复查/仅查看/阻止/未支持。 |
| `OptimizationActionPolicy._candidate_route` | 按明确来源区分 Stage 1、4D3、三种直接清理根及独立 Bin；错类别或来源拒绝。 |
| `OptimizationActionPolicy._decision` | 构造固定能力的复查决定，不生成执行授权。 |
| `OptimizationActionPolicy._blocked` | 构造 NONE/BLOCKED 和有限原因。 |
| `optimization_source_digest` | 对完整报告规范 JSON 计算 SHA-256，绑定证据与来源，不只绑定可变标题。 |
| `RecommendationActionResolver.__init__` | 注入策略与时钟，限制报告有效期为 1–1800 秒。 |
| `RecommendationActionResolver.resolve` | 深度重新验证报告模型、时间、唯一建议；调用独立策略，返回摘要绑定路由。过期/伪引用拒绝。 |

## 能力注册表、导航与应用装配

| 函数 | 作用、输入输出与副作用 |
|---|---|
| `DomainPreparationService.capability / available / prepare` | Protocol：声明一个固定准备能力、报告可用性、返回复查上下文；接口没有执行/确认/提权方法。 |
| `capability_surface` | 固定映射能力到 Qt 页面枚举；NONE 或未知能力抛拒绝异常。 |
| `OptimizationDomainCapabilityRegistry.__init__` | 创建空的、未封闭的注入表。 |
| `OptimizationDomainCapabilityRegistry.register` | 只接受有限能力，拒绝重复、NONE 及封闭后的注册。 |
| `OptimizationDomainCapabilityRegistry.seal` | 结束装配，防止后续替换准备实现。 |
| `OptimizationDomainCapabilityRegistry.capabilities` | 返回规范顺序的已注册能力元组。 |
| `OptimizationDomainCapabilityRegistry.available` | 仅在 seal 后查询准备服务；缺失则 false，不回退到其他执行器。 |
| `OptimizationDomainCapabilityRegistry.prepare` | 检查取消/可用性，调用准备接口，再校验返回 route/rec/domain/surface/context 和取消状态。 |
| `OptimizationActionRouter.__init__` | 注入报告仓库、解析器、封闭注册表、失效管理器和审计器。 |
| `OptimizationActionRouter.inspect` | 仅从本地报告仓库解析 UUID，检查时效/失效/可用性并审计路由或拒绝。 |
| `OptimizationActionRouter.prepare` | 复核来源摘要与失效状态，调用准备接口，返回前再检查来源；不解析任何业务确认。 |
| `OptimizationHandoffStore.__init__` | 创建有容量上限的会话内上下文仓库和锁。 |
| `OptimizationHandoffStore.save` | 深度校验、清理过期条目，拒绝重复/容量溢出/过期；仅写内存。 |
| `OptimizationHandoffStore.take` | 原子移除一个 context_id，核对 route_id 与到期；不匹配也不可重放。 |
| `OptimizationHandoffStore.clear` | 退出时清空导航意向；不操作原业务事务。 |
| `DomainReviewPreparationService.__init__ / capability / available` | 绑定固定能力与本地来源解析器，声明准备能力可用，不等于系统动作可执行。 |
| `DomainReviewPreparationService.prepare` | 前后检查取消和 route，保存单次上下文，返回 NEEDS_TARGET_SELECTION；目标 Fresh 检查尚待原业务执行。 |
| `RepositoryDomainReviewResolver.__init__` | 注入共享 runtime 仓库，不创建新项目/数据库路径。 |
| `RepositoryDomainReviewResolver.resolve` | 残留解析精确报告、候选及有效卸载；个人文件解析 Stage 1 记录、当前授权和原 scan root；其他类型只传递本地候选 ID，不自动选对象。 |
| `build_optimization_review_services` | 装配封闭能力表、路由、会话、单次导航、失效及结果协调器，返回独立四工具注册表。 |
| `ApplicationRuntime.create_optimization_review_services` | 同一应用内懒创建并复用一个复查服务束。 |
| `AnalysisResultRepository.get_record` | 只读查询一个 matches_plan 的 Stage 1 记录；不存在或不属于候选则拒绝，调用方仍需检查当前授权。 |

## 会话与持久化

| 函数 | 作用、输入输出与副作用 |
|---|---|
| `OptimizationSessionService.__init__` | 注入日志仓库/路由/审计并创建每清单取消标志与锁。 |
| `OptimizationSessionService.create` | 检查最多 50 个唯一建议以及同一来源摘要；创建本地清单记录，不创建全局确认。 |
| `OptimizationSessionService.get` | 从数据库读一个不可执行的清单摘要。 |
| `OptimizationSessionService.prepare` | 仅 PENDING 可进入；原子保留唯一活动复查后释放锁做可取消准备，再保留结果。失效/失败/取消不复用旧授权。 |
| `OptimizationSessionService.skip` | 跳过尚未派发的复查；不能覆盖已完成结果或活动业务。 |
| `OptimizationSessionService.close_review` | 核对 handoff 后标记 REVIEWED；关闭窗口不代表成功。 |
| `OptimizationSessionService.cancel` | 取消准备标志和未开始条目，保留已开始业务和结果；不终止卸载器、不自动 Undo。 |
| `OptimizationSessionService.accept_correlated_outcome` | 内部结果接收端，核对 handoff/建议/重放，先审计再持久化；只由结果协调器调用。 |
| `OptimizationSessionService._replace` | 更新一个条目并推导清单状态；保留 CANCELLED/STALE，用版本比较避免覆盖并发更新。 |
| `OptimizationSessionService._item / _require_open` | 精确解析清单成员、拒绝已经关闭的清单。 |
| `optimization_sessions._encode / _decode` | 深度校验 JSON、计算/校验 SHA-256，并核对 ID/版本/状态列。摘要用于损坏检测，不声称防御同用户恶意改库。 |
| `OptimizationSessionRepository.__init__` | 创建独立 SQLAlchemy 连接管理，复用同一应用数据库。 |
| `OptimizationSessionRepository.initialize` | quick_check、加法建表、把未结束清单置为 STALE；从不恢复执行。 |
| `OptimizationSessionRepository.create` | 仅 revision=1 可创建；UUID 冲突不覆盖。 |
| `OptimizationSessionRepository.get` | 读取并完整校验摘要；未知、损坏或数据库错误拒绝。 |
| `OptimizationSessionRepository.save` | 要求连续版本并进行数据库 compare-and-swap；并发修改报错。 |
| `OptimizationSessionRepository.close` | 释放连接，不删除历史。 |

## 业务结果、失效与刷新

| 函数 | 详细作用 |
|---|---|
| `OptimizationDomainResultReader.__init__ / close` | 创建/释放专用读取连接，连接启用 query_only，不持有业务 writer。 |
| `optimization_receipts._tables` | 在固定枚举表内选择已有事务和确认表；不接受用户提供表名或 SQL。 |
| `OptimizationDomainResultReader.read` | 对库做完整性检查，读取唯一业务事务，校验机制、风险、恢复、实际验证与确认血缘；返回脱敏 DomainReceiptSnapshot。 |
| `OptimizationDomainResultReader._aware` | 把 SQLite 保存的 UTC 无时区 datetime 恢复为明确 UTC。 |
| `OptimizationDomainResultReader._consumed_lineage` | 检查 R2 计划/即时确认属于同一事务和计划、父子关系正确、已消费，且即时 Preview 匹配当前业务记录。 |
| `OptimizationDomainResultReader._outcome` | 将各业务的真实终态与验证摘要映射到七种结果；未终态返回 None；进程已自然退出不冒充 Agent 执行动作。 |
| `OptimizationDomainResultReader._read_files` | Stage 2 专用读取：分离 R1 文件操作与 R2 回收站，检查逐项完成、计划确认、Undo 或手动恢复记录摘要。 |
| `OptimizationDomainResultReader._verified_cleanup_items` | 对照预留条目核验每项结果的 item/operation/source UUID、顺序、终态与验证状态；其他对象的成功记录不能替代本项。 |
| `DomainResultReader.read` | 注入式只读结果契约；调用方只能提供事务引用，不能提供验证结论。 |
| `OptimizationOutcomeCoordinator.__init__` | 注入真实结果读取器、清单服务、失效管理器；内存关联不可跨重启恢复。 |
| `OptimizationOutcomeCoordinator.bind` | 只关联 handoff 之后产生、尚未派发的一份业务 Preview；绑定计划/机制/领域，拒绝历史、错域、重复、清空与普通清理互用。 |
| `OptimizationOutcomeCoordinator.collect` | 重读已绑定事务，检查不可变身份、计划、风险和恢复没变，保留不确定状态，审计并汇入清单，使旧领域建议失效。 |
| `RecommendationInvalidationService.__init__` | 注入时钟和容量上限，无全局系统事件总线。 |
| `invalidate_by_domain / invalidate_by_snapshot / invalidate_by_target_identity` | 分别按领域时间、Snapshot、精确本地建议引用使旧证据失效。 |
| `RecommendationInvalidationService.require_current` | 过期、已失效或容量失控时拒绝路由。 |
| `RecommendationInvalidationService._bound` | 超限后永久 fail-closed，不能通过淘汰旧失效记录重新激活建议。 |
| `optimization_refresh_goal` | 选择固定 Stage 3 只读意图；不执行、不重跑全套 E1、不扩大文件扫描根。 |
| `compare_optimization_observations` | 比较新旧完整同类采样：启动项数量、可用内存、软件条目或相同卷集合空间；未知/不完整保持未测量，禁止因果归因。 |
| `MsixUninstallRepository.transition` | 新增可选 result 参数：仍遵守原事务状态，仅存匹配事务的脱敏 MSIX 验证摘要；不新增卸载能力。 |
| `ProcessTargetResolver._require_selected_identity` | 比较用户所选行的创建时间和执行文件路径，拒绝 PID 复用；不是仅看进程名。 |

## 审计与工具

| 函数 | 作用 |
|---|---|
| `OptimizationActionAuditLogger.__init__` | 注入现有审计仓库和可选 Git commit。 |
| `route_decided / prepared / rejected` | 分别记录路由决定、handoff 和稳定拒绝原因；无文件名、命令、密钥或正文。 |
| `outcome_recorded` | 记录清单、建议、handoff、domain plan/transaction/confirmation、结果、风险与恢复；实际动作标注 domain_confirmation。 |
| `OptimizationReviewTool.__init__ / manifest` | 声明 R0 输入输出 Schema、平台、取消和批量上限；没有 writer guard。 |
| `OptimizationReviewTool.execute` | 校验取消并重新验证输入模型，只调用固定本地处理函数。 |
| `build_optimization_action_registry` | 注册恰好四个复查工具；内部 inspect/prepare/create/refresh 分别委托路由或清单读取，不复制业务执行。 |

## Qt 控制器函数

GUI 展示层不调用系统工具。准备/执行仍在既有业务 worker 或新增纯准备 worker 中运行。

| 函数 | 用户可见作用与边界 |
|---|---|
| `OptimizationReviewWorker.__init__ / run` | 在后台准备一个清单成员；通过 completed/failed 信号返回，取消由共享会话服务负责。 |
| `ObservedDomainDialog.publish_domain_preview` | 只发送机制+事务 UUID，让结果读取器关联；不发送确认 token、参数或伪造结果。 |
| `OptimizationDomainReviewDialog.__init__ / _build_destination` | 按固定页面枚举嵌入原业务 UI；服务入口只读，软件由原路由器选机制。 |
| `_build_personal_storage / _files_selected` | 将原授权根带入新 Stage 1；仅把新报告中勾选文件交给原 Stage 2 页面，不直接写文件。 |
| `_route_software` | 使用现有 software router，E3 不决定 exe、args 或 fallback。 |
| `_observe_dialog / _preview_ready` | 观察子窗口和新 Preview 引用；不支持的原业务结果明确提示不能汇总验证。 |
| `refresh_result` | 仅读取关联事务；None/异常不代表成功。 |
| `_open_refresh` | 选择该领域的新 R0 刷新窗口，仍需新确认；源报告过期时不制造前后对比。 |
| `shutdown / closeEvent / _finished` | 幂等请求原控制器安全停止，accept/reject 也停止轮询；子业务窗口仍活动时提示先处理；不会杀死卸载器。 |
| `OptimizationRefreshDialog.__init__ / _report_ready / closeEvent` | 准备单领域只读计划、展示非因果观测、关闭时取消读取；不显示动作按钮。 |
| `SystemOptimizationTab._populate_recommendations` | 显示结构化路由状态与默认未勾选建议；按 UUID 绑定行，排序不改变引用。 |
| `create_review_session / open_next_review` | 创建显式勾选清单、每次准备下一条；不自动执行或自动继续下一项。 |
| `_review_prepared / _review_failed` | 在 UI 线程验证后台准备结果并再次检查来源；取消后不打开窗口，失败明确显示。 |
| `_review_closed / cancel_review_session / _show_review_session` | 记录复查结束或取消后续、展示逐项状态；没有结果不得显示已验证。 |
| `FileAnalysisTab.__init__ / refresh_paths` | 可接收限定的 authorized_root_ids；优化复查只展示原授权根，其他普通入口行为保持原样。 |
| `SystemDiagnosticsTab._open_selected_process_action` | 从新报告读取所选 PID 的时间/路径证据；缺失则要求重新采集，准备时再验证。 |
| 各业务 `_prepared` / `_preview_completed` / `_show_prepared` 回调 | 原功能不变，新增 Preview UUID 通知；原计划/即时确认仍是唯一授权。 |
