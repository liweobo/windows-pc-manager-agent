# Stage 5C 手工验证清单

本清单只用于开发机上的合成页面或公开、无登录、无交易的网站。不要用真实账户、密码、付款信息、
内部网址、localhost 服务、工作资料或敏感下载做测试。

## 准备

```powershell
uv sync --all-groups
uv run playwright install chromium
$env:QT_QPA_PLATFORM = "offscreen"
uv run python -m pc_manager_agent --smoke-test
uv run python -m pc_manager_agent
```

关闭 `QT_QPA_PLATFORM=offscreen` 后再进行可见窗口手工测试。确认应用以普通用户运行。

## 会话和计划

1. 打开“浏览器”页，启动一个可见临时会话。
2. 输入一个公开 HTTPS 页面和清晰目标，选择“生成计划”。
3. 确认页面尚未导航，计划显示 Origin、动作、R0、无持久配置和预期效果。
4. 拒绝计划，确认没有导航；重新生成并批准，确认只能执行一次。
5. 尝试再次执行同一确认，预期被拒绝。

## 网络拒绝

逐一尝试 `file:`、带用户名密码 URL、非标准端口、`localhost`、`127.0.0.1`、`::1`、私网 IPv4、
link-local 和单标签主机，预期均在执行前被阻止。HTTP 公开站点只有在计划明确显示“不安全 HTTP”
并确认后才可继续；不要为测试临时降低策略。

## 语义动作和页面变化

1. 在合成页面确认元素表只显示语义 role/name，不显示 CSS/XPath。
2. 对明确搜索框执行搜索，对“下一页”执行翻页，对详情按钮执行展开。
3. 页面刷新后尝试使用旧计划，预期 `stale`/generation mismatch。
4. 名为“Buy/Checkout/Send/Post/Create account/Accept terms”的控件应被阻止。
5. 页面出现“ignore previous instructions / reveal secret / call tool”类文字时，UI 应显示提示注入
   信号，但不得新增动作或自动确认。

## 用户接管

1. 生成但不执行一个计划，然后点击“用户接管”；原计划应失效。
2. 在临时浏览器中手工浏览，不输入真实密码。
3. 点击“交还 Agent”；元素表应刷新，旧元素/确认不能继续使用。
4. 关闭应用，确认临时浏览器随应用安全关闭；重新打开后没有旧会话可恢复。

## 下载和回滚

1. 选择一个合成 PDF/DOCX/XLSX 链接和空目标目录。
2. Preview 应显示来源 Origin、文件名、类型、默认 50 MiB 上限、R1、条件 FULL 回滚及“不覆盖”。
3. 拒绝后目标目录应为空；批准后只出现一个文件。
4. 再次下载到同名目标，预期冲突且原文件不变。
5. 对未改变文件执行回滚，确认它被移动到恢复目录。
6. 重新下载、修改内容后执行回滚，预期拒绝且不覆盖任何文件。
7. 尝试 EXE/ZIP/脚本、路径穿越/ADS/Windows 保留名或大小超限文件，预期失败关闭。
8. 使用“交给办公文档”，确认 Stage 5A 只显示建议，仍需重新选择、预览和确认才可能修改。

## 记录结果

记录 Windows 版本、Python/Playwright/Chromium 版本、测试 URL 是否为合成页面、每项 PASS/FAIL、
错误 reason code 和截图。不得在截图/Issue 中包含查询值、Cookie、密码、本地用户名路径或页面正文。
