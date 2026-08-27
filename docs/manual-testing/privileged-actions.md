# Stage 4X3 privileged-action manual test guide

Use this guide only on a disposable Windows 11 virtual machine with a snapshot. Automated tests deliberately
do not show UAC, change real service configuration, edit HKLM or uninstall software.

## Prerequisites

1. Build the isolated Broker with `scripts/build-privileged-broker.ps1` and configure development-mode exact
   path/hash as described in `privileged-broker-uac.md`.
2. Run the Main application normally, not “as administrator”. Confirm Task Manager reports medium integrity
   for Main. Never use a production computer or important software for these checks.
3. Create three disposable targets you own: a signed ordinary test service whose startup type is Manual or
   Automatic, a third-party HKLM Run value pointing to a harmless signed test executable, and a simple
   machine-scope MSI test package with no shared dependencies or valuable data.
4. Take a VM snapshot and export the relevant original configuration through normal Windows administration
   tools. Do not use protected, Microsoft, security, network, driver, Agent or enterprise-managed targets.

## Service startup type

1. Select the disposable service and request Manual → Automatic or Automatic → Manual.
2. Verify the ordinary flow reports insufficient permission, then opens a new R3 Preview rather than reusing
   the R2 confirmation.
3. Confirm the Preview names one service, shows Administrator, `FULL`, current/target type and states that
   runtime status will not change.
4. Approve the plan. Change the service configuration externally before immediate confirmation and verify the
   action is blocked as stale. Restore the VM state and repeat.
5. Approve both confirmations, cancel UAC, and verify no change and no second prompt.
6. Repeat and accept UAC. Verify the type changes, the running/stopped state remains identical, Broker and
   Main verification pass, and the Broker exits.
7. Use restore history. Verify a clean restore works; then repeat with an external conflict and confirm
   `RESTORE_CONFLICT` without overwrite.

## HKLM Run disable and restore

1. Refresh Startup Management and choose only the disposable HKLM Run entry. Confirm the Preview shows the
   exact 32- or 64-bit registry view, one object, R3, Administrator and `FULL`.
2. Cancel at plan confirmation, immediate confirmation and UAC in separate trials. Each must leave the value
   unchanged and must not retry.
3. Accept UAC and verify only that exact value disappears while sibling values and the other registry view
   remain unchanged. Refresh the page and confirm an Agent recovery record exists.
4. Create a different value with the same name before restore. Restore must stop on conflict and must not
   overwrite it. Remove the conflict, create a fresh restore plan, accept UAC and verify the exact original
   type/bytes return to the same view.

## Machine MSI uninstall

1. Select the disposable machine MSI in Software inventory. Confirm routing opens Stage 4X3 rather than the
   current-user Stage 4D2A dialog.
2. Confirm the Preview shows exactly one product, R3, Administrator and rollback `NONE`. There must be no
   command, raw UninstallString, custom arguments, process-kill or service-stop option.
3. Test UAC cancellation and confirm no retry. Test an externally changed version/registration and a running
   related service; both must block before dispatch.
4. Restore the clean snapshot, accept both confirmations and UAC, complete the interactive MSI UI, and verify
   success is reported only after both fresh software and Windows Installer inventories show the original
   identity absent.
5. Confirm related files are not deleted by the Agent and recovery text describes manual reinstall, not Undo.

## Evidence to retain

Retain only sanitized screenshots and audit event IDs: action type, plan/request IDs, manifest/schema/policy
versions, confirmation/UAC outcome, Broker and Main verification, rollback level and final state. Do not copy
raw registry value bytes, uninstall strings, full local paths, SID, pipe/rendezvous values, nonces, HMAC keys,
provider secrets or user data into issues or commits.
