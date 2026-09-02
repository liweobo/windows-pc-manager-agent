# Stage 5D 多 Agent 架构与能力边界

## 核心结论

这里的“多 Agent”不是多个模型自由聊天和执行。Agent 只能理解、分析并返回结构化建议；真实操作仍
由既有业务域依次完成 Fresh 目标解析、安全策略、Preview、计划确认、即时确认、确定性执行和结果
验证。Agent 消息、TaskGraph、安全审查意见和 Memory 都不是授权。

```text
用户请求
  -> 本地请求分流
  -> 最小 Agent 选择
  -> 有界 TaskGraph
  -> 最小 Context + Scoped Memory
  -> 结构化建议 / DomainPreparationProposal
  -> 原业务域重新解析目标
  -> Safety -> Preview -> Confirmation -> Executor -> Verification
  -> 确定性结果聚合 -> 元数据审计
```

## 组件类型

| 类型 | 负责什么 | 不负责什么 |
|---|---|---|
| LLM Agent | 意图理解、图草案、解释、建议 | 权限判定、确认、系统写入、成功判定 |
| Deterministic Service | 任务图校验、能力校验、Context、Memory、路由 | 根据自由文本执行操作 |
| Domain Executor | 执行已注册且已授权的一个业务动作 | 接受 Agent 消息作为授权 |
| Adapter | 封装 Windows/Office/Browser 的窄接口 | 任意命令或跨域操作 |
| Policy | 默认拒绝并验证身份、范围、风险和数据流 | 被 Agent 投票或 Memory 覆盖 |

## Agent 拓扑与能力矩阵

所有角色由运行时创建并绑定清单摘要和 Prompt 版本。清单未列出的输入、输出、工具、Memory 范围
和委派均拒绝。所有角色的 `may_execute_actions` 与 `may_request_confirmation` 固定为 `false`。

| Agent | 可读数据 | 可提出/使用 | Memory | 明确禁止 |
|---|---|---|---|---|
| Orchestrator | 公共、用户、系统元数据 | 分解、调度、Domain handoff、聚合；可委派 Planner/Domain/Verifier | 低风险全局偏好 | 工具执行、确认、UAC |
| Planner | 公共、用户数据 | 结构化图草案；最终角色由本地代码重写 | 低风险全局偏好 | PID、路径、ProductCode、服务身份、授权令牌 |
| Safety Reviewer | 本地结构化数据 | 调用确定性图/目标策略并报告 | 无 | 执行、确认、覆盖策略 |
| File | 已批准文件域元数据 | 4 个 Stage 1 R0 只读工具、文件域准备建议 | 全局 + File | 移动、重命名、回收站的直接执行 |
| System | 系统只读元数据 | Stage 3 R0 工具、系统域准备建议 | 全局 + System + UI | 结束进程、改启动项/服务 |
| Software | 软件只读元数据 | Stage 4D1/4D3 R0 工具、软件域准备建议 | 全局 + Software | 卸载或残留清理执行 |
| Office | 已选文档及本地元数据 | `office.document.read`、Office 域准备建议 | 全局 + Office | 写文档、浏览器上传、COM/Shell |
| Browser | 公共/用户/网页数据 | 会话打开、导航、观察的 R0 建议 | 全局 + Browser | 下载执行、任意点击/脚本、外传文档 |
| Optimization | 系统元数据 | Stage 4E1/E3 R0 分析/复查工具 | 全局 + System | 清理、结束进程、修改服务 |
| Verifier | 本地结构化证据 | 按证据优先级聚合 | 无 | 用模型意见代替业务验证 |
| Memory Manager | 低风险用户数据 | 有限 key 的查看、经确认保存、更新、删除、清空 | 按用户动作访问全部 Memory scope | 保存密钥、权限或确认偏好 |
| Audit Manager | 公共及结构化元数据 | 写入 ID、摘要、版本、计数 | 无 | 保存 Prompt、Context 正文、Memory 值、秘密 |

## TaskGraph

`TaskGraph` 保存易失的用户目标和持久化安全的目标摘要，节点通过 `TaskDependency` 构成有向无环图。
本地校验器检查节点数、深度、未知引用、环、Domain/Role 所有权，以及所有执行节点是否拥有上游
`WAIT_FOR_CONFIRMATION`。图中的执行节点只表示未来顺序，不能证明确认已存在。

简单单域任务只选择 Orchestrator 与一个 Domain Agent；多域任务才增加 Planner 和 Verifier。
独立只读资源可以并行持有 read lease；同一资源只要任一 lease 是 write 就会串行化。lease 只用于
进程内调度，不能替代业务域持有的文件句柄、事务锁或 Fresh 检查。

## 委派协议

`AgentDelegationRequest` 只携带目标代码、引用、子集能力和递减预算，不携带正文、确认令牌、Broker
秘密或任意参数字典。协调器验证：

1. 父 Agent 的清单摘要和 Prompt 身份仍匹配；
2. 子角色在父清单委派列表内；
3. 请求能力是目标角色工具集合的子集；
4. 目标摘要、Task ID 与引用仍在根边界内；
5. 深度、总委派次数、模型调用和 Context 预算未耗尽；
6. 请求未过期且只消费一次，并且消费者正是最初签发时绑定的父 Agent 实例。

`AgentMessageEnvelope` 由运行时创建 sender/recipient 身份，校验 payload 的 task/node/goal/role，并携带
去不掉的 trust labels。网页、文档和模型派生标签会继续传播；Agent 消息禁止声明
`SYSTEM_TRUSTED`。

## 目标、失败、取消与验证

`TaskGoalBoundaryPolicy` 将图、委派、工具建议限制在根目标允许的 Domain、工具和引用集合。网页中
“卸载软件”或文档中“上传本文件”的文字只是数据，不会扩展这个集合。

模型失败最多按节点配置重试一次，且重试只允许重新生成建议；破坏性 Domain action 永不由 Agent
自动重放。取消根任务只停止未来协调并释放进程内 lease，已经完成的业务操作不会假装被撤销。
重启把未完成任务标为 `INTERRUPTED`，不恢复易失目标或继续执行。

结果按 `DOMAIN_VERIFIED > DOMAIN_OBSERVED > USER_SUPPLIED > MODEL_INFERENCE` 选择；多数模型意见
不能覆盖确定性证据，模型来源的 `*_VERIFIED` 会让总结果保持部分完成。

## V1 限制

- Stage 5D 不新增任何系统写工具、通用 Executor、Shell、管理员 Agent 或全局确认。
- 当前 GUI 把单域请求登记到 Task Center 并创建安全的 Domain preparation handoff；完整多域 receipt
  关联、暂停/恢复和崩溃后的工作流重建留给 Stage 5E。
- OpenAI 图适配器是可选且未自动接入 UI；未来启用前仍需独立的外部数据披露确认。
- 任务日志只保存摘要；无法在重启后恢复用户目标或自动继续任务。
