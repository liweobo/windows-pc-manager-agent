# Roadmap

## Stage 0 — secure foundation (complete)

GUI/tray, provider boundary, structured plans, tool registry, risk review,
confirmation, audit, rollback contract, R0 metadata scanner, tests, and CI.

## Stage 1 — read-only analysis (complete)

Large-file reporting, cautious idle-file evidence and confidence, staged
duplicate detection, exports, richer filtering, user-managed roots/exclusions,
structured model planning, aggregate-only explanation, audit, and a 10,000-file
benchmark. Installed-software inventory remains an unimplemented MVP item and is
planned as a separate read-only Stage 1B/Stage 3 increment.

## Stage 2 — safe file operations (not started)

Directory creation, move, rename, conflicts, verified undo records, two-level
confirmation, Windows recycle-bin integration, and result verification. Permanent
deletion remains prohibited.

Recommended next increment: **Stage 2A**, limited to conflict-safe file move and
rename commands with complete undo records and automatic rollback. Recycle-bin
work should remain a separate Stage 2B review because its recovery is generally
manual rather than FULL.

## Stage 3 — system status

Read-only disk, CPU, memory, process, startup, service, software, and performance
views. No system mutation.

## Later stages

Each R3 capability receives an independent threat model, implementation, test
plan, and review. Voice, office/browser automation, schedules, and cross-platform
adapters follow only after the safety boundary is stable.
