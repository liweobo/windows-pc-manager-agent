# ComputerTask lifecycle

## Root states

`CREATED` and `PLANNING` build and validate the bounded graph. `AWAITING_PLAN_CONFIRMATION` means no node has
been dispatched. Approval moves to `READY`; explicit start consumes that approval and moves to `RUNNING`.

Normal waiting states are not failures:

- `WAITING_FOR_USER`: target selection, manual review, budget/recovery decision or Fresh review.
- `WAITING_FOR_DOMAIN_CONFIRMATION`: the owning domain must show and resolve its own exact confirmation.
- `WAITING_FOR_USER_TAKEOVER`: a visible browser/vendor interaction requires the user; handback is Fresh.
- `PAUSED`: future scheduling is stopped. Already completed actions are unchanged and external processes are not
  killed.

`CANCELLING` drains only orchestration work, then becomes `CANCELLED` or `PARTIALLY_COMPLETED`. `INTERRUPTED` is
assigned during repository startup to any task that was active when the previous process ended. It can enter
`RECOVERING`, but recovery finishes in a user decision, partial, blocked, failed or cancelled state—never directly
back in `RUNNING`.

Terminal states are `CANCELLED`, `PARTIALLY_COMPLETED`, `COMPLETED`, `FAILED` and `BLOCKED`. They are immutable.

## Node states and dependencies

Nodes use `PENDING`, `READY`, `RUNNING`, `WAITING_CONFIRMATION`, `COMPLETED`, `PARTIAL`, `FAILED`, `BLOCKED`,
`CANCELLED` and `INTERRUPTED`. V1 dependencies are hard, soft or optional. A hard dependency must yield a terminal
recorded outcome before its dependent is considered. Failure policies are finite: stop, continue partial, wait for
the user, or retry a bounded safe R0 read.

Every status mutation creates a new immutable root revision and an append-only checkpoint. Graph revision creates
a new graph ID/version and preserves prior graph/checkpoint/receipt history. The old task-plan confirmation is
invalidated. A model, web page, document, file name or Memory value cannot create a valid `TaskRevisionRequest`;
the request is an explicit user event and scope expansion has its own acknowledgement.

## Confirmation separation

Task-plan confirmation is bound to task ID, graph ID/version/digest, goal digest, policy digest, scope digest,
read-only node IDs and expiry. It is single-use. It does not satisfy or replace domain plan confirmation, runtime
confirmation, external-data disclosure, user takeover or UAC. Restart and graph revision invalidate it.
