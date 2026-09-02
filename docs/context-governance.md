# Stage 5D Context 治理与跨域数据流

## 最小 Context

`ContextGovernanceService` 不复制完整聊天、全部 Memory、整份文档或整个网页。调用方提供与当前
Task/Node 相关的 `ContextItem` 引用，服务逐项验证来源、信任标签、数据分类、Agent 清单、跨域规则
和预算。超出预算时整项省略，不做可能改变含义的静默截断。

默认限制为 8 条消息、12,000 字符、4 个文档块、4 个网页块、8 条 Memory 和 32 个结构化引用；
任务图最多 32 个节点、深度 8。配置只能在 Pydantic 硬上限内收紧或调整。

## 信任标签与污染传播

| 来源 | 必需标签 |
|---|---|
| 用户目标、Memory | `USER_SUPPLIED` |
| 本地结构化结果、recent reference | `LOCAL_STRUCTURED_DATA` |
| 系统策略 | `SYSTEM_TRUSTED` |
| 文档块 | `UNTRUSTED_DOCUMENT` |
| 网页块 | `UNTRUSTED_WEB` |
| 模型摘要 | `MODEL_GENERATED` |

非系统策略内容不能自称 `SYSTEM_TRUSTED`。派生摘要取所有来源标签的并集，并追加
`MODEL_GENERATED`；因此网页或文档经过另一个 Agent 总结后仍然不可信。信任标签说明来源，不能
成为执行权限。

## 数据分类与跨域规则

- `CREDENTIAL`、`SECRET` 永远阻止进入 Agent Context 或跨域传递。
- `SENSITIVE` 默认阻止。
- 文档正文只进入 Office/Verifier 的任务内 Context；外部传输默认阻止。
- 网页正文只进入 Browser/Verifier；其他角色最多得到 opaque reference。
- 本地系统元数据可进入 System、Software、Optimization、Office 或 Verifier，用于报告。
- 公共数据和普通用户偏好只在当前任务内传递；外部传输仍需业务域自己的披露流程。

例如，系统指标可以作为结构化引用进入 Office 报告；浏览器下载的 PDF 只产生提示，Office 必须
重新选择、读取和确认；文档内容不能借 BrowserAgent 发送到网站。

## Secret 防护

分类为 Secret/Credential 的项目整包失败关闭；此外还阻止常见 `api_key=...`、`password=...`、
`Authorization: ...`、Bearer、Cookie、Token 和 MFA 赋值模式。该检查是保守的已知模式检测，不是
完整 DLP，调用方仍不得把原始凭据、Confirmation secret 或 Broker secret 交给服务。

## Recent reference

`RecentEntityReferenceStore` 只在内存中保存短时、按 conversation 隔离的 identity digest hint。
“刚才那个文件”可先解析为 hint，但 `requires_fresh_resolution` 永远为 true：File/Office/Browser 等
拥有该对象的业务域必须重新解析当前身份，旧 PID、DOM reference、文件身份或软件清单不能直接
执行。达到数量上限时淘汰最旧项，过期、跨会话和重启后引用均拒绝。

## 审计

审计记录 Task/Node/Agent ID、图/目标/清单摘要、Prompt 版本、角色、数量、状态和固定原因码；不记录
用户目标正文、网页/文档块、Agent Prompt、模型输出正文、Memory 值或任何秘密。
