"""Finite shared text/voice request routing. No tool registry, Windows adapter or consent API."""

import re
import unicodedata

from pc_manager_agent.domain.user_requests import RequestDomain, RequestRoute, UserRequest
from pc_manager_agent.orchestration.process_action_planner import process_target_query
from pc_manager_agent.orchestration.service_action_planner import service_action_intent
from pc_manager_agent.orchestration.system_diagnostic_planner import (
    classify_diagnostic_intent,
    is_diagnostic_request,
)
from pc_manager_agent.orchestration.system_optimization_planner import is_optimization_request
from pc_manager_agent.safety.voice import SensitiveTranscriptRedactor


class UserRequestDispatcher:
    """Classify preparation/navigation only; ambiguity is explicit and never chooses targets."""

    def route(self, request: UserRequest) -> RequestRoute:
        """Return a finite destination; its own policy determines execution risk."""
        text = unicodedata.normalize("NFKC", request.text).strip().casefold()
        domain = self._classify(text)
        if (
            domain is RequestDomain.AMBIGUOUS
            and request.context.active_surface is RequestDomain.PROCESS
        ):
            domain = RequestDomain.PROCESS
        return RequestRoute(request_id=request.request_id, domain=domain)

    def _classify(self, text: str) -> RequestDomain:
        if SensitiveTranscriptRedactor().contains_sensitive(text) or any(
            term in text
            for term in (
                "powershell",
                "cmd.exe",
                "exec(",
                "eval(",
                "run_command",
                "admin shell",
                "忽略安全",
                "绕过",
                "永久删除",
                "permanently delete",
                "background_listen",
                "microphone.start",
            )
        ):
            return RequestDomain.BLOCKED
        command = text.strip("。.!！?？ \n\t")
        if command in {
            "取消",
            "停止",
            "别执行了",
            "停止后续操作",
            "cancel",
            "stop",
            "stop next actions",
        }:
            return RequestDomain.CANCEL
        if command in {"进度", "查看进度", "现在怎么样", "status", "progress"}:
            return RequestDomain.STATUS
        if re.match(r"^(确认|是的|执行吧|我承担风险|我知道风险|yes\b|confirm\b|i accept\b)", text):
            return RequestDomain.CONFIRMATION
        if any(join in text for join in ("然后", "接着", " and then ")):
            return RequestDomain.AMBIGUOUS
        if any(
            term in text for term in ("浏览器自动", "点击购买", "打开网页", "browser automation")
        ):
            return RequestDomain.UNSUPPORTED
        if any(term in text for term in ("服务", "service")):
            return (
                RequestDomain.SERVICE
                if service_action_intent(text) is not None
                else RequestDomain.DIAGNOSTICS
            )
        if any(term in text for term in ("开机启动", "启动项", "startup")):
            return RequestDomain.STARTUP
        if any(term in text for term in ("卸载", "uninstall")):
            return RequestDomain.SOFTWARE
        if any(
            term in text
            for term in (
                "文档",
                "预算",
                "表格",
                "工作表",
                "xlsx",
                "docx",
                "csv",
                "pdf",
                "markdown",
                "excel",
                "document",
            )
        ):
            return RequestDomain.OFFICE
        if "回收站" in text and any(term in text for term in ("清空", "empty")):
            return RequestDomain.RECYCLE_BIN_EMPTY
        if "recycle bin" in text and "empty" in text:
            return RequestDomain.RECYCLE_BIN_EMPTY
        if any(
            term in text
            for term in ("清理缓存", "清除缓存", "清理残留", "clean cache", "cleanup", "缓存都删")
        ):
            return RequestDomain.CLEANUP
        if is_optimization_request(text) or any(
            term in text for term in ("优化", "变慢", "卡顿", "optimize", "slow", "磁盘空间分析")
        ):
            return RequestDomain.OPTIMIZATION
        if any(term in text for term in ("进程", "process", "退出应用", "退出程序")) and any(
            term in text for term in ("关闭", "结束", "终止", "退出", "close", "kill", "terminate")
        ):
            return RequestDomain.PROCESS
        if any(term in text for term in ("关闭", "关掉", "close ", "kill ", "删掉它")):
            if "它" in text or " it" in text:
                return RequestDomain.AMBIGUOUS
            try:
                process_target_query(text)
            except ValueError:
                return RequestDomain.AMBIGUOUS
            return RequestDomain.PROCESS
        if is_diagnostic_request(text) or any(
            term in text
            for term in ("内存", "cpu", "进程", "memory", "磁盘", "c盘", "c 盘", "disk", "诊断")
        ):
            return RequestDomain.DIAGNOSTICS
        if any(
            term in text
            for term in ("移动", "重命名", "改名", "整理", "撤销", "回滚", "rename", "move ")
        ):
            return RequestDomain.FILE_OPERATIONS
        if any(term in text for term in ("回收站", "删除", "delete", "trash", "recycle")):
            return RequestDomain.TRASH
        if any(term in text for term in ("文件", "扫描", "大文件", "重复", "闲置", "file", "scan")):
            return RequestDomain.FILES
        return RequestDomain.UNSUPPORTED


def diagnostic_preparation_goal(text: str) -> str:
    """Preserve the finite requested collector category without journaling a voice transcript."""
    return {
        "OVERVIEW": "系统状态只读诊断",
        "PERFORMANCE": "性能只读诊断",
        "CPU": "CPU 只读诊断",
        "MEMORY": "内存只读诊断",
        "DISKS": "磁盘只读诊断",
        "PROCESSES": "进程只读诊断",
        "STARTUP": "启动项只读诊断",
        "SERVICES": "服务列表只读诊断",
        "SOFTWARE": "已安装软件清单只读诊断",
    }[classify_diagnostic_intent(text).value.upper()]


def optimization_preparation_goal(text: str) -> str:
    """Keep explicit finite evidence categories without uploading/journaling full voice input."""
    lowered = text.casefold()
    groups = (
        ("空间分析", ("空间", "清理", "磁盘", "c盘", "disk")),
        ("后台负载", ("后台", "background")),
        ("响应性能", ("响应", "responsiveness")),
        ("开机慢", ("开机", "启动", "boot")),
    )
    selected = [label for label, markers in groups if any(term in lowered for term in markers)]
    if "开机慢" not in selected and any(term in lowered for term in ("慢", "卡", "slow")):
        selected.append("电脑变慢")
    return "、".join(selected) or "快速检查"
