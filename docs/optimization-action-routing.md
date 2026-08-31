# Stage 4E3：建议复查与跨模块编排

本阶段不是“一键优化器”。它将 Stage 4E1 的结构化建议转成进入现有业务模块的复查入口，
不创建新的系统执行器。报告文字、文件名、软件名、UUID、勾选和复查清单都不是执行权限。

## 数据流与边界

```text
本地报告 UUID + 建议 UUID
  → 报告存在性 / 完整摘要 / 时效 / 结构化证据检查
  → 固定能力注册表 + 建议路由策略
  → 单次导航上下文（NEEDS_TARGET_SELECTION）
  → 原业务重新读取当前对象、用户选择、独立 Safety + Preview
  → 原业务计划确认 / 必要的即时确认 / 原业务决定是否进入 Stage 4X
  → 原业务执行 / 验证 / 审计 / 恢复记录
  → E3 只读关联业务事务与确认记录
  → 用户可另行确认一个最小 R0 刷新计划
```

准备返回 `NEEDS_TARGET_SELECTION` 不表示目标已通过 Fresh 检查。优化层不接受裸 PID、路径、
ProductCode、卸载字符串、命令、参数数组、force、administrator 或确认 token。
具体现行对象和业务权限只在原模块产生。服务在 V1 只允许查看，不显示服务修改按钮。

## Recommendation → Review Routing Matrix

| 类型 | 入口 | 进入后还需要做什么 |
|---|---|---|
| REVIEW_TEMP_STORAGE | Stage 4E2 | 精确当前用户 Temp 来源，Fresh 发现、再次默认不勾选、独立 R2 双确认 |
| REVIEW_APPLICATION_CACHE | Stage 4E2 | V1 仅 DirectX Shader Cache；浏览器/未知缓存不可直接路由清理 |
| REVIEW_CRASH_DUMPS | Stage 4E2 | 精确当前用户 CrashDumps；近期、锁定、受保护对象仍被阻止 |
| REVIEW_RECYCLE_BIN | 独立 Stage 4E2 Bin empty | 精确系统卷快照、两次独立确认，R2_HIGH_IMPACT、NONE |
| REVIEW_LARGE_FILES / REVIEW_INACTIVE_FILES / REVIEW_DUPLICATES | Stage 1 → Stage 2 | 重新检查原目录授权、重新扫描和选择；随后独立移动、重命名或回收站流程 |
| REVIEW_STARTUP_ITEM | Stage 4B | 当前清单、重新选择身份、备份、Fresh Preview；停用/恢复分别确认 |
| REVIEW_HIGH_RESOURCE_PROCESS | Stage 3 → Stage 4A | 新 R0 清单；PID+创建时间+可执行路径匹配；先审查正常关闭，强制终止不自动继续 |
| REVIEW_INSTALLED_SOFTWARE | Stage 3 → Stage 4D | 新软件清单、用户选择、现有确定性 MSI/Vendor/winget/MSIX 路由与各自双确认 |
| REVIEW_SOFTWARE_RESIDUAL | Stage 4D3 → Stage 4D4 | 一个有效卸载上下文；重新分析精确已知路径，用户重新选择，独立 R2 流程 |
| REVIEW_SERVICE | Stage 3 只读服务列表 | REVIEW_ONLY；不进入 Service 控制界面 |
| FREE_DISK_SPACE | Stage 3 磁盘概览 | REVIEW_ONLY；没有混合批量清理 |
| NO_ACTION_NEEDED | 无动作 | INFORMATIONAL，不能生成执行计划 |
| MANUAL_REVIEW / 旧版未分类建议 | 无动作 | 仅解释；不从文字猜测路由 |

残留建议按上游报告分组，避免把不同卸载的所有者证据混到一起。受保护的数据库、配置、用户数据、
共享位置和未知来源不因“所有权可信”而变成可删除对象。软件卸载后不会自动追加残留分析/清理。

## 三个独立注册表

Stage 4E1 固定五个 R0 工具，Stage 4E2 固定四个工具，均保持不变。Stage 4E3 新注册表只有：

- `optimization.recommendation.inspect`：检查本地引用和可进入的复查类型。
- `optimization.recommendation.prepare_action`：准备单次导航，不执行系统动作。
- `optimization.session.create`：记录最多 50 条显式选择的建议。
- `optimization.session.refresh`：读取清单日志，不隐式扫描系统或文件。

这四个工具没有 writer guard。没有 apply-all、execute-recommendation、force、admin、通用 Shell，
也不把任意字符串当成业务工具名称。实时指标刷新使用单独的新 Stage 3 R0 计划与确认。

## 会话、确认与取消

会话只组织步骤：CREATED → IN_PROGRESS → PARTIALLY_APPLIED / COMPLETED；也可能 CANCELLED / STALE。
每项分别记录 PENDING、ROUTED、REVIEWED、BLOCKED、FAILED、STALE、SKIPPED、NO_LONGER_APPLICABLE
或 EXECUTED。COMPLETED 表示清单已处理完，不表示每项建议都成功执行。

同一清单最多一个活动复查。关闭窗口只记 REVIEWED，不能记 APPLIED_VERIFIED。数据库使用版本号
比较更新，防止取消被并发结果覆盖。重启将未结束的清单标记 STALE，丢弃内存导航/关联上下文，
不重放业务操作。已完成的结果仍保留；取消不会自动恢复启动项或清理数据，也不会杀死外部卸载器。

报告最长 30 分钟，源报告、Snapshot 和系统快照任一过期就拒绝。建议失效不删除历史。
原业务状态变化后使对应领域旧建议失效；没有全局事件总线，外部变化最终仍由业务 Fresh 检查拦截。

## 结果、恢复与观测

结果关联在原业务创建 Preview 后、执行前绑定一次事务，绑定不得指向历史完成事务。读取端只查询
有限业务表，检查计划 ID/摘要、事务时间、实际验证结果和原业务确认。R2 使用原业务已消费的即时
确认及计划关联；R1 文件操作使用原业务持久化的计划确认和完整 Undo 证据。

计划确认所见 Preview 与即时 Fresh Preview 可以不同：两者必须绑定同一不可变计划；即时确认必须
匹配当前业务 Preview。不能通过简单要求两个 Preview 哈希相同来替代原业务的时效校验。

`APPLIED_VERIFIED` 仅表示原业务验证了动作状态；不表示电脑更快。其余状态保留不确定、阻止、取消、
失败或已不适用。仅退出码为 0 不足以认定卸载完成。MSIX 新事务额外保存脱敏验证摘要，历史没有此
摘要的结果不能补造“已验证”。

文件移动/重命名和启动项恢复是有条件 FULL；回收站移动为 MANUAL；卸载与清空回收站为 NONE。
没有“撤销整个优化清单”。恢复必须进入原模块生成新计划和确认；恢复冲突不能覆盖。

用户点击独立刷新时只准备相应 Stage 3 意图：启动项列表、内存/进程、软件列表或磁盘空间；
原 Stage 3 必需的 system-info collector 保留。不重跑 Stage 4E1 完整扫描，不读文件内容。
只有完整、可比较的前后采样才能显示测量值；卷集合或容量改变、部分采集、未知状态都显示未测量。
短期观测归因始终 UNKNOWN/LOW，不输出速度、启动耗时改善百分比。回收站移动不等于释放磁盘空间。

## V1 明确限制

- 建议主要来自汇总观察；不会从旧报告自动选择具体进程、软件或启动项。
- 一个 handoff 只关联第一份新业务 Preview；用户另行开启的 Force、Restore 或再次操作不拼接为批量权限，
  后续动作以对应业务日志为准，需要新复查才能单独汇入清单。
- 原业务仍可自行进入既有 Stage 4X one-shot 路径；E3 不决定管理员权限、不减少 UAC，也不创建特权会话。
  当前汇总读取器不读取 Stage 4X 的特权结果，因此界面明确提示以原业务结果为准，不声称已验证。
- 指标刷新显示在独立只读窗口，尚不持久化为长期收益时间序列；会话保持 BENEFIT_NOT_MEASURED。
- 新会话只能引用当前进程内有效报告；重启后不能从历史报告恢复执行。
- 真实危险操作没有在开发机器上手工执行；自动化仅使用临时文件和合成系统适配器。

## 手工验证（普通用户）

1. 启动应用，在“系统优化分析”生成、确认并运行 R0 计划。
2. 在“建议”勾选一条，创建复查清单；确认此时系统没有修改。
3. 点击“复查下一条”，核对入口符合上表且要求重新选择当前对象。
4. 在隔离测试目录/虚拟机中验证原业务的拒绝、取消与成功路径，不拿系统服务或安全软件试验。
5. 返回查看清单：窗口关闭不能显示成功；部分失败不应导致自动下一步或全局回滚。
6. 生成独立只读刷新计划：检查范围、重新确认，观察界面不应承诺性能提升。
