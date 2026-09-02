# Stage 5D 用户 Memory 模型

## Memory 不是权限

Memory 仅改善连续使用体验。即使保存了“常用 Downloads”或“Office 默认 Save As”，每次真实操作
仍需新的目标解析、计划、安全审查、Preview 和该业务域要求的确认。Memory 不能降低风险、取消
确认、授予管理员权限、生成 Broker authority 或授权工具执行。

## V1 可保存内容

Memory key 是封闭枚举：回复语言、大文件阈值、闲置天数、Office 输出格式、Office 保存模式、语音
输出模式、默认 UI 页面、常用目录 UUID 引用、常用应用普通名称和模型供应商名称。任意自然语言
指令、PID、DOM element、确认令牌和原始路径都不是有效 key。

Scope 分为 `GLOBAL_PREFERENCE`、`FILE`、`OFFICE`、`VOICE`、`BROWSER`、`SYSTEM`、`SOFTWARE` 和
`UI`。Agent 只能按矩阵请求最小 scope；Safety Reviewer、Verifier 和 Audit Manager 无读取权限。

## 写入决策

`MemoryWritePolicy` 先核对 key 对应的 category/scope、有限值格式、来源、置信度和敏感等级：

- `BLOCK`：Secret、Sensitive/Prohibited、“以后无需确认/绕过安全”等指令或格式错误；
- `EPHEMERAL_ONLY`：模型候选、行为推测、非高置信或没有明确用户意图；
- `REQUIRE_USER_CONFIRMATION`：通过全部检查的明确用户偏好；
- `ALLOW` 保留在枚举中，但 V1 不对持久化写入使用无确认直通。

密码、API key、Cookie、Token、MFA、私钥、文档/网页正文、完整对话、银行或医疗信息不会作为
Memory 保存。已知 secret 模式会直接阻止；这不是完整敏感信息检测，UI 和调用方也必须限制输入。

## 生命周期与用户控制

Memory 使用独立 SQLite 表，不与 Audit 混用。相同 logical key 更新同一记录并增加 version；可选 TTL
到期后不会被查询，并写入不含 value 的 EXPIRED 事件。用户可在 Memory 页面查看来源、scope、key、
version、到期时间和值，明确确认后保存/编辑/删除、按 scope 清空或清空全部。

关闭 Memory 后，Agent 查询返回空且拒绝新写入，但用户仍能查看和删除已有记录；关闭 Memory 不会
删除 Audit。删除与清空会物理删除 value，只保留不含值的元数据事件，不创建含原值的 tombstone。

## 审计与存储说明

Audit 只记录 candidate/memory ID、key digest、决策、scope、数量和固定原因码。Memory 数据库目前
继承应用本地数据目录和 Windows 用户 ACL，没有实现独立字段加密；因此 V1 只允许低风险配置值。
OpenAI/API 密钥继续来自环境变量或未来的系统凭据存储，绝不进入 Memory 表。
