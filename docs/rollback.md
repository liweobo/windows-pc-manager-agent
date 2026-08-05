# Rollback design

Stage 0 does not modify scanned files, so `file.scan` truthfully declares
`RollbackLevel.NONE`: nothing needs undoing. This is different from claiming that
an operation cannot be recovered.

Future write operations must implement `OperationCommand`:

1. `execute()` after all gates;
2. `verify()` against concrete postconditions;
3. `build_undo_record()` from observed before/after state;
4. `rollback()` only while stated validity conditions hold.

An `UndoRecord` includes operation ID, original/new paths, file identifier,
before/after metadata, timestamp, rollback level, validity conditions, and the
rollback result. Move and rename should target FULL. Recycle-bin recovery must be
MANUAL unless a tested platform API proves reliable automatic restoration.

## Code rollback

Use a new branch and revert the completed commit:

```powershell
git switch -c fix/revert-stage-0
git revert <commit-sha>
git push -u origin fix/revert-stage-0
```

Do not force-push or use `git reset --hard` as the normal recovery procedure.
