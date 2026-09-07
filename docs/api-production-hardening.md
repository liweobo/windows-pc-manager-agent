# Stage 7A production-hardening API

This document describes every public function/method introduced by the completed Stage 7A release-policy,
database-migration, logging, crash and diagnostic slices. Private helpers are included where they enforce a security
boundary. Packaging/signing APIs will be added when those slices are implemented.

## Version and production policy

| API | Purpose and contract |
|---|---|
| `BuildMode` | Finite `DEVELOPMENT`, `TEST` and `PRODUCTION` identity. It is not inferred from feature flags. |
| `ReleaseFeature` | Closed feature vocabulary. Unknown strings cannot become capabilities. |
| `FeatureFlags.is_enabled(feature)` | Checks immutable membership; it does not consult model/environment text. |
| `FeatureFlags.require(feature)` | Raises `FeatureDisabledError` before a disabled route can create task/plan state. |
| `FeatureFlags.development_defaults()` | Returns every existing domain for source development while preserving each domain's original safety layer. |
| `FeatureFlags.private_rc_defaults()` | Returns the fixed read-only private-RC allow-list: file, system, software and optimization analysis. |
| `ProductionRuntimeContext` | Holds independently observed frozen/elevation/executable/environment facts used by validation. |
| `ProductionConfigValidator.validate(settings, context)` | Returns every stable policy violation: non-frozen production, elevation, unsafe flags/provider/Broker/path/hash or forbidden debug/dev environment. Safe mode requires an empty capability set and disabled provider. |
| `ProductionConfigValidator.require_valid(settings, context)` | Fails startup with `ProductionConfigurationError` when any violation exists. |
| `ReleaseEvidence` | One explicit PASS/FAIL/NOT_RUN/NOT_CONFIGURED item; missing evidence is never success. |
| `ReleaseGateEvaluator.required_checks(level)` | Returns the immutable evidence set required for one readiness level. |
| `ReleaseGateEvaluator.evaluate(requested, evidence)` | Rejects duplicate/empty evidence, requires exact PASS status and computes the highest truthful readiness grade. |
| `ApplicationRuntime.require_feature(feature)` | Shared deterministic domain guard used before preparation/persistence. |
| `MainWindow._apply_feature_flags()` | Hides disabled tabs from the release UI; it is presentation only and never grants authority. |
| `MainWindow._route_blocked(feature)` | Rejects disabled chat/navigation routes before task/plan persistence and explains the fixed release block. |
| `_release_feature_for_request(domain)` | Maps the finite request-domain enum to the release feature checked before task registration. |
| `reduce_to_safe_mode(settings)` | Returns a new configuration with empty feature flags, provider disabled, no key/model and no Broker path/hash. It can only remove capability. |

## Migration boundary

| API | Purpose and contract |
|---|---|
| `MigrationManager.__init__(database_path, app_version)` | Binds one exact SQLite target and application version. |
| `MigrationManager.migrate()` | Runs quick/foreign-key checks, validates version/schema state, retains a verified legacy backup, performs explicit transactional migration and returns `MigrationReport`. Future, drifted, corrupt or interrupted databases fail closed. |
| `MigrationBackupService.create_verified_backup(database_path)` | Creates a SQLite online backup at an absent Agent-owned path and verifies integrity/hash before migration writes. |
| `MigrationBackupService.restore_to_new_path(evidence, destination)` | Verifies recorded SHA/integrity and copies only to an absent recovery target. It never overwrites or silently replaces the live database. |
| `MigrationReport` | Immutable evidence with from/to/config versions, schema digest and optional retained backup metadata. |
| `create_current_schema(connection)` | Creates the full known current schema from one catalog before repositories initialize. |
| `expected_table_names()` | Returns the fixed table-name set used for completeness checks. |
| `MigrationManager._initialize_fresh_database()` | Creates a new current schema transactionally and verifies its ready record/digest. |
| `MigrationManager._migrate_existing(...)` | Walks adjacent registered steps only; missing transitions fail and retain recovery evidence. |
| `MigrationManager._apply_step(step, final=...)` | Applies one exact step under an explicit transaction and writes version/readiness metadata atomically. |
| `MigrationManager._verify_current_database(record)` | Rejects table or digest drift even when version numbers look current. |
| `_require_healthy_database(database_path)` | Rejects failed quick/foreign-key checks before reads or backups. |
| `_schema_digest(connection)` | Canonically hashes the actual SQLite schema without trusting object order. |

## Logging API

| API | Purpose and contract |
|---|---|
| `LogRedactionPolicy.__init__(path_salt=None, max_text_chars=1024)` | Creates a bounded recursive policy. Random process-local salt prevents stable path correlation unless tests inject a salt. |
| `LogRedactionPolicy.redact(value, key=None)` | Converts arbitrary metadata to bounded JSON-safe values and removes values under credential/content keys. |
| `LogRedactionPolicy.is_sensitive_key(key)` | Conservatively recognizes credential, voice, transcript, prompt, document and webpage-body keys. |
| `LogRedactionPolicy.redact_text(value)` | Removes assignments, Bearer/known tokens, URL queries and Windows paths; truncates oversized text. |
| `LogRedactionPolicy.path_reference(path)` | Produces a salted 16-hex path reference rather than logging username/path components. |
| `StructuredJsonFormatter.format(record)` | Emits stable one-line JSON. Traceback source and locals are intentionally absent. |
| `LoggingRuntime.close()` | Detaches, flushes and closes exactly the handler installed by this runtime. |
| `configure_application_logging(...)` | Creates a bounded rotating local logger after reparse checks. Production cannot enable debug output. |
| `_identifier(value, fallback)` | Allows only bounded identifier syntax for component/event/trace/task labels. |

## Crash API

| API | Purpose and contract |
|---|---|
| `LocalCrashReporter.__init__(...)` | Configures local report location and a 1–50 retention bound. |
| `LocalCrashReporter.capture(type, exception, traceback)` | Exclusively writes sanitized exception facts and hashed frames, fsyncs and prunes; never uploads. Failure raises `CrashEvidenceError`. |
| `LocalCrashReporter.install(chain_previous=False)` | Installs a process exception hook and returns the prior hook so shutdown/tests can restore it. Production leaves chaining off so raw exceptions do not reach an unknown hook; development may opt in. |
| `LocalCrashReporter._prune()` | Deletes only excess Agent-owned, non-reparse `crash-*.json` reports. |
| `CrashLoopGuard.__init__(...)` | Sets bounded unclean-start threshold/window and permits a deterministic test clock. |
| `CrashLoopGuard.begin_session()` | Counts a prior active marker as unclean, prunes the window, writes the current marker and returns a reduction-only decision. Corrupt history recommends safe mode. |
| `CrashLoopGuard.mark_clean_exit()` | Removes the current marker and clears unclean history after orderly shutdown. |
| `CrashLoopGuard._read_history(now)` | Reads finite timestamps; malformed state yields a threshold-sized fail-safe history. |
| `CrashLoopGuard._atomic_json(destination, payload)` | Uses exclusive temporary creation, fsync and replace for Agent-owned health metadata. |
| `_safe_function_name(value)` | Prevents attacker-controlled exception metadata from becoming an unbounded frame label. |

## Diagnostic bundle API

| API | Purpose and contract |
|---|---|
| `DiagnosticBundleService.__init__(...)` | Configures bounded local input, Preview TTL and mandatory audit callbacks. Pending bytes and authority live only in process memory. |
| `DiagnosticBundleService.prepare(output_path, metadata, recent_error_codes, now=None)` | Validates an absent local ZIP target, builds exact sanitized members in memory and returns an expiring R1 Preview. No file is created. |
| `DiagnosticBundleService.approve(preview, now=None)` | Revalidates exact Preview/expiry, calls mandatory confirmation audit, then issues a hashed, single-use volatile authority. |
| `DiagnosticBundleService.reject(preview)` | Audits rejection before discarding volatile members. It creates no file. |
| `DiagnosticBundleService.export(authority, now=None)` | Atomically consumes authority, rechecks content/target, exclusively commits and verifies the ZIP. Result-audit failure removes the newly created target. |
| `DiagnosticBundleService._sanitized_logs()` | Reads only bounded Agent JSONL log files from a safe non-reparse log directory. |
| `DiagnosticBundleService._sanitize_log_line(raw_line)` | Selects structural fields only; message/details/exception text are dropped. Malformed lines become one fixed error code. |
| `DiagnosticBundleService._validate_target(target)` | Rejects relative, traversal, ambiguous, non-ZIP, existing, missing-parent, network and reparse targets. |
| `DiagnosticBundleService._discard_expired(now)` | Physically drops expired volatile member bytes and never refreshes their authority. |
| `build_runtime_metadata(settings, migration, frozen_binary, signing_status=...)` | Builds a finite manifest without username, hostname, path or credential values. |
| `_validate_error_codes(codes)` | Allows at most 50 stable, bounded uppercase identifiers; arbitrary error text is rejected. |
| `_entries_digest(entries)` | Canonically hashes names, lengths and member bytes to bind review to output. |
| `_verify_archive(path, entries)` | Reopens the ZIP and proves exact names/bytes; extra, missing or corrupt members fail. |
| `_contains_reparse_component(path)` | Walks target parents without following a new scope and rejects reparse components. |
| `export_diagnostic_bundle(parent, runtime)` | GUI flow: choose target, Preview exact members/exclusions, default No, reject/approve, export and report local-only result. |
| `_recent_error_codes(runtime)` | Derives a bounded set of structural event codes from failed audit rows; it never reads error bodies. |

## Entry point and safe-mode UI

| API | Purpose and contract |
|---|---|
| `build_parser()` | Exposes only version, smoke test and reduction-only safe mode. It has no force/admin/debug/feature-widening switch. |
| `run_application(settings, smoke_test=False)` | Acquires single-instance ownership, evaluates crash-loop/safe-mode reduction, validates production configuration, starts logging/crash evidence, migrates runtime storage, constructs the appropriate UI and cleans up deterministically. |
| `main(argv=None)` | Loads environment settings and uses temporary state for source smoke tests. |
| `SafeModeWindow.__init__(runtime)` | Constructs a separate minimal three-tab recovery surface and no domain tabs/workers. |
| `SafeModeWindow.attach_tray(tray)` | Allows tray navigation only to the recovery window. |
| `SafeModeWindow.request_quit()` | Marks intentional shutdown; no business worker exists to cancel. |
| `SafeModeWindow.shutdown()` | Reports immediate local shutdown and never claims a domain result. |
| `SafeModeWindow.closeEvent(event)` | Preserves hide-to-tray behavior without adding an execution channel. |

## Audit redaction update

`audit.redaction.is_sensitive_key`, `redact_text` and `redact_json` retain their original public contracts but now
also reject MFA/OAuth/Bearer/OpenAI/GitHub/JWT shapes and voice/document/web content keys. This is defense in depth;
callers must still avoid placing content or credentials in an `AuditEvent`.
