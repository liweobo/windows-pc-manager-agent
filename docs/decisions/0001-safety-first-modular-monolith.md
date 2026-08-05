# ADR 0001: Safety-first modular monolith

Status: Accepted

## Context

The product needs multiple conceptual Agent roles but deterministic safety and a
small personal desktop deployment. Distributing early components into processes
or making each role an LLM would increase failure modes and test cost.

## Decision

Use one Python process with explicit modules and injected interfaces. Planning may
use an LLM provider; review, confirmation, execution, verification, audit, path
checks, and rollback remain deterministic Python. UI work uses Qt workers.

## Consequences

Core workflows are fast to test without GUI/network access. Boundaries can later
move behind IPC if a privileged broker is designed. Module boundaries must be
kept strict because the runtime does not enforce process isolation.
