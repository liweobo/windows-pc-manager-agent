# Stage 5E manual validation checklist

These checks use only application navigation and disposable test data. Do not approve an actual uninstall,
service/startup change, process termination, cleanup, Recycle Bin emptying, UAC or external-data upload merely to
test the Final Orchestrator.

1. Start the app as a standard user and open **主页与长任务**. Confirm five templates are shown and none claims
   unattended operation.
2. Create **电脑健康检查**. Confirm it appears in **任务中心** as
   `AWAITING_PLAN_CONFIRMATION` and no domain action runs.
3. Select it and choose **审查并确认选中任务计划**. Confirm the dialog says it approves only R0 coordination and
   excludes domain writes. Reject once and verify the task is cancelled without opening an operation.
4. Create another task and approve it. Confirm the owning read-only page opens and still requires that page's
   normal plan/Fresh workflow.
5. Pause a running test task. Confirm new scheduling stops. Resume and verify a Fresh-review attention item is
   created instead of immediate execution.
6. Cancel a task and confirm the warning says completed work is not undone and external uninstallers are not
   killed.
7. With a disposable task waiting at a domain handoff, close the app normally or simulate process interruption.
   Restart. Confirm `INTERRUPTED`, old confirmation invalidation and **检查中断任务** behavior. Verify nothing is
   replayed.
8. Open the tray menu. **打开任务中心** may navigate only. A notification must not approve anything.
9. Review the Audit page. Task events should contain IDs, digests, fixed codes and counts, not raw goals, paths,
   document/web bodies, confirmation secrets or model reasoning.

Record OS build, Python/app version, standard/elevated token state, exact steps and screenshots. Automated fake
tests do not substitute for this UI review, but this checklist intentionally avoids dangerous real actions.
