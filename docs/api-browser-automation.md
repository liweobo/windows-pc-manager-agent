# Stage 5C Browser Automation API reference

This reference documents every Stage 5C production function and method. Browser page text, accessible
names, links, filenames and errors are untrusted input. None of the APIs below grant authority merely
because a model, page or user supplied a string: execution requires the typed domain model, deterministic
policy, an exact plan and, where required, a durable confirmation.

## Domain models

### `domain.browser`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserElementReference.verify_fingerprint()` | Pydantic post-validator. Recomputes SHA-256 from `role`, accessible name and optional `href`; returns the validated instance. It raises `ValueError` if any semantic field was changed after issuance. No I/O. |
| `BrowserElementReference.create(...)` | Creates a session/page/navigation-bound semantic reference from an accessibility role, name and optional link. It computes the fingerprint locally and never accepts or exposes a CSS/XPath selector. No browser I/O. |
| `BrowserObservation.bind_elements()` | Pydantic post-validator. Requires every element to belong to the observation's exact session, page and navigation generation. A copied or stale reference raises `ValueError`. No I/O. |
| `BrowserActionRequest.enforce_shape()` | Enforces the closed action vocabulary: only navigation carries a URL, only search/filter carry text, element actions carry one matching reference, and HTTP approval is navigation-only. Invalid combinations raise `ValueError`; there is no generic argument map. |
| `BrowserActionRequest.canonical_digest()` | Serializes every action field as canonical sorted JSON and returns SHA-256. Confirmations and plans bind this value; it is an integrity digest, not a secret or signature. |

`BrowserActionKind` is the complete action vocabulary. It contains session lifecycle, explicit navigation,
back/forward/reload/observe, semantic link/search/filter/pagination/expand, one-document download and manual
takeover actions. It intentionally contains no generic click, selector, JavaScript, CDP, HTTP request,
purchase, message-send or credential action. `BrowserDecision`, `BrowserSessionState`, `BrowserElementRole`,
`WebContentTrust` and `PromptInjectionSignal` are closed enums. The remaining models are immutable evidence:
`BrowserPolicyResult`, `BrowserActionResult`, `BrowserSessionDescriptor`, `BrowserModelSummaryRequest` and
`BrowserModelSummary`. Model summaries are advisory and cannot approve or execute an action.

### `domain.browser_plans` and `domain.browser_downloads`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserTaskPlan.canonical_digest()` | Hashes the complete immutable plan, including action, policy, origin, expected effect and optional download Preview digest. Any plan edit changes the digest and invalidates old approval. |
| `BrowserDownloadPreview.canonical_digest()` | Hashes the exact source identity, page generation, element, declared type/size, destination, limit, risk and rollback promise for a single download. No file I/O. |

`BrowserConfirmationRecord` binds a confirmation to plan/action digests, origin, browser generation and
expiry. `BrowserDownloadIdentity` records the committed path, size, SHA-256 and detected MIME type.
`BrowserDownloadRecoveryRecord` records conditional FULL recovery for an unchanged file.
`BrowserWorkerDownload` describes the worker-owned temporary file before Main-process validation; it is not
proof that the file is safe or committed.

## Configuration and service composition

### `config.browser`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserSecuritySettings.__post_init__()` | Validates positive observation, element, redirect, timeout, download-size and confirmation limits. Invalid configuration raises `ValueError` during startup. No external I/O. |

Defaults bound visible text to 40,000 characters, semantic elements to 5,000, links to 200, controls to
100, tables to 10, redirects to 10, plan approval to 300 seconds and a document to 50 MiB.

### `app.browser`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserServices.close()` | Closes the orchestration service and confirmation repository. It cancels/closes only the Browser Worker owned by this application and releases SQLite resources. Repeated close is safe through lower-level lifecycle guards. |
| `build_browser_services(database_path, audit_repository, git_commit=None, adapter=None)` | Constructs URL/action/download policies, the isolated worker adapter by default, five-tool registry, SQLite confirmation repository, audit logger and `BrowserTaskService`. A supplied adapter is intended for deterministic tests. It initializes browser authority storage but does not launch Chromium until `start()`. |

`ApplicationRuntime.create()` calls `build_browser_services`; `ApplicationRuntime.close()` calls
`BrowserServices.close()`. `ApplicationRuntime.browser` is the UI-facing composition root and must not be
replaced by direct tool calls.

## URL, content, action and download safety

### `safety.browser.network`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `SystemHostResolver.resolve(hostname)` | Uses the operating system resolver to return all unique A/AAAA strings. Resolution failure or an empty answer raises `BrowserNetworkPolicyError`. It performs DNS I/O but no HTTP request. |
| `_origin(scheme, hostname, port)` | Builds a canonical origin with IPv6 brackets and omits only the scheme's default port. Pure helper. |
| `BrowserUrlPolicy.__init__(resolver=None)` | Injects a resolver; production uses `SystemHostResolver`, tests use a fake. No resolution occurs in the constructor. |
| `BrowserUrlPolicy.validate(url, allow_http=False)` | Parses and normalizes one URL, IDNA-encodes the host, rejects credentials/fragments/non-HTTP schemes/nonstandard ports/single-label hosts, requires explicit HTTP permission, resolves every address and requires all to be globally routable. Returns `ValidatedBrowserUrl`; raises a reason-coded `BrowserNetworkPolicyError` on any ambiguity. |
| `BrowserUrlPolicy.validate_redirect_chain(urls, allow_http=False)` | Validates every hop independently and rejects an empty or over-limit chain. Returns immutable validated hops. It deliberately re-resolves every hostname. |
| `BrowserUrlRedactor.redact(url, keep_safe_query_names=False)` | Produces audit-safe scheme/origin/path output. Query values and fragments are removed; safe query names may be retained without values. Malformed input produces a fixed redacted token rather than reflecting data. |
| `_is_ip_literal(hostname)` | Returns whether a hostname parses as IPv4/IPv6; used to keep literal-address handling explicit. Pure helper. |

The URL policy is an application-layer SSRF control, not an operating-system network sandbox. A hostile
same-user process, compromised browser runtime or DNS behavior outside the policy remains a residual risk.

### `safety.browser.content` and `safety.browser.actions`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserPromptInjectionDetector.detect(visible_text)` | Case-insensitively scans bounded visible text for instruction override, secret request, tool instruction, authority claim and exfiltration phrases. Returns advisory signals only; signals never grant or expand an action. |
| `envelope_untrusted_content(content, max_characters=12000)` | Truncates remote text to a positive bound and wraps it in explicit untrusted-content markers for an optional model request. Non-positive limits raise `ValueError`. It does not call a model. |
| `BrowserActionPolicy.review(action, current_origin)` | Deterministically maps one typed action to ALLOW, confirmation, takeover or BLOCK with risk/rollback truth. It verifies origin boundaries, allowed roles and safe accessible-name intent, blocks transactional/account/message terms and requires R1 plan confirmation for download. No page content can override it. |
| `_action_origin(action)` | Extracts the normalized origin of an explicit URL or referenced `href`; returns `None` when no target URL exists. Pure helper used by policy. |
| `_r0_allow(reason)` | Builds the standard R0/rollback-NONE/no-runtime-confirmation allow result. Pure helper. |
| `_block(reason)` | Builds a fail-closed R2/rollback-NONE blocked result. It does not execute or audit by itself. |

### `safety.browser.downloads`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserDownloadPolicy.validate_filename(filename)` | Normalizes a leaf filename and rejects empty/dot names, traversal, directories, ADS colons, Windows reserved names, control/bidirectional characters, trailing dot/space, unsupported extension and overlength. Returns the safe leaf or raises `BrowserDownloadPolicyError`. |
| `BrowserDownloadPolicy.validate_declared(filename, mime_type, size_bytes, maximum)` | Runs filename checks, requires the declared MIME type to match the finite extension map and rejects negative/oversized declarations. It returns the safe filename; declaration is not final content proof. |
| `BrowserDownloadPolicy.validate_completed(preview, temporary_path)` | Requires a regular, non-symlink temporary file, hashes it within the Preview limit, verifies magic bytes and extension/MIME agreement, and returns SHA-256. It reads only the worker temporary file and fails closed on mismatch. |
| `BrowserDownloadManager.__init__(policy=None)` | Injects the finite download policy. No filesystem I/O. |
| `BrowserDownloadManager.commit(preview, temporary_path)` | Revalidates content, creates the destination directory, creates the target with exclusive/no-overwrite semantics, streams the staged bytes, verifies size/hash and returns identity plus recovery record. It does not delete staging; orchestration retains that artifact separately. A conflict or changed content fails without overwriting. |
| `BrowserDownloadManager.retain_staged(temporary_path, reason=...)` | Moves an exact regular staged file beneath its private `.pc-manager-recovery` directory using a UUID name. It is used instead of permanent deletion after validation or a safe filename mismatch. |
| `BrowserDownloadManager.rollback(record)` | Hashes the current committed file and requires its path, size and SHA-256 to remain exactly unchanged. It creates an application recovery directory and moves the file without overwrite. Returns the recovery path or raises a reason-coded error on drift/conflict. |
| `_hash_file(path, maximum)` | Streams a file in 1 MiB chunks, enforces the byte limit during reading and returns `(size, sha256)`. It raises when the actual stream exceeds the limit. |
| `_retain_artifact(path, reason)` | Requires one regular non-symlink artifact, creates a private recovery directory, chooses an absent UUID name and renames without a delete fallback. A conflict or invalid source fails closed. |
| `_magic_matches(suffix, head)` | Checks a bounded header against PDF, ZIP-based Office, PNG, JPEG, GIF, WebP or no-NUL text signatures. Returns a boolean; it is a format guard, not malware scanning. |
| `_detected_mime(suffix)` | Maps the finite accepted extension set to one normalized MIME value. Unsupported suffixes cannot reach it through policy. |

## Planning, confirmation and execution

### `orchestration.browser.BrowserPlanCompiler`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `__init__(url_policy, action_policy)` | Injects deterministic safety dependencies. No I/O. |
| `navigation(session, url, user_goal_summary=...)` | Normalizes and resolves one destination, detects the narrow explicit-HTTP case, builds a typed navigation action, runs policy and returns a digestible plan. Any other network or policy failure stops plan creation. |
| `semantic_action(session, observation, kind, element=None, text=None, user_goal_summary=...)` | Builds one action bound to the current page generation, reviews it against the current origin and returns a plan. Generic click/selector or mismatched element data cannot be represented. Blocked policy raises `BrowserWorkflowError`. |

### `orchestration.browser.BrowserTaskService`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `__init__(...)` | Injects registry, compiler, policies, confirmation/guard/download/audit components, temporary root and cancellation token. It owns session-local state but does not start the browser. |
| `session` | Read-only property returning the current descriptor or `None`. |
| `observation` | Read-only property returning the current bounded observation or `None`. |
| `start(headless=False)` | Calls only registered `browser.session.open`, stores the ACTIVE session and audits lifecycle through the composed tools. It rejects a duplicate active session. |
| `prepare_navigation(url, user_goal_summary=...)` | Requires an active session and delegates to `BrowserPlanCompiler.navigation`; it records plan review but does not navigate. |
| `prepare_action(kind, element=None, text=None, user_goal_summary=...)` | Requires current observation, compiles one semantic action and audits the plan. It does not execute it. |
| `request_confirmation(plan)` | Requires current session/page bindings and creates one short-lived durable PENDING confirmation. Returns its record; no execution authority is consumed yet. |
| `resolve_confirmation(confirmation_id, approved)` | Persists APPROVED or REJECTED and audits the decision. It cannot change the plan. |
| `execute(plan, confirmation_id)` | Rechecks session/generation/URL/action policy, atomically consumes the exact approval, calls only the matching registered tool, validates result/session/action IDs, updates observation and audits the result. Any drift, replay, storage failure or mismatch raises and no fallback is attempted. |
| `observe()` | Calls registered `browser.page.observe`, validates/stores bounded output and audits it. It cannot mutate the page. |
| `begin_user_takeover()` | Invalidates pending authority, creates and reviews a takeover action, executes registered `browser.element.activate`, and changes the session state to USER_TAKEOVER. Agent-originated actions are blocked until handback. |
| `end_user_takeover()` | Executes the finite handback action, obtains a fresh observation/new navigation generation, invalidates old element references and restores ACTIVE state. It does not read credentials entered by the user. |
| `prepare_download(element, destination_directory, user_goal_summary=..., declared_size_bytes=None)` | Requires a current link reference, derives and validates a safe filename/type, creates an exact R1 Preview and a plan bound to its digest, and audits review. It creates no destination file. |
| `execute_download(plan, preview, confirmation_id)` | Revalidates plan/Preview/session/generation/policy, consumes approval and local single-use write guard, downloads into the Agent temporary root through the registered download tool, validates and exclusively commits one file, audits SHA-256/size and returns identity plus recovery record. |
| `rollback_download(record)` | Delegates conditional FULL recovery to `BrowserDownloadManager.rollback`, audits success and returns the recovery path. It refuses changed files and never overwrites. |
| `invalidate_pending_authority()` | Invalidates all non-consumed approvals for the active session in SQLite and returns the affected row count. Used on cancellation, takeover and close. |
| `cancel()` | Sets the local cancellation token, invalidates authority and asks the adapter to cancel/close its owned work. It never kills an unrelated browser. |
| `close()` | Invalidates pending authority and closes the owned adapter/session. No session can be resumed after restart. |
| `_set_observation(observation)` | Enforces exact session identity before updating session-local observation. Mismatch raises `BrowserWorkflowError`. |
| `_fresh_validate_action_url(action)` | Re-runs URL/DNS validation for explicit navigation or linked element targets immediately before execution. No cached DNS result grants authority. |
| `_require_current_generation(action)` | Requires page/navigation/reference IDs to equal the latest observation; stale actions fail. |
| `_require_plan_session(plan)` | Requires plan action/session to match the active session and latest page generation. |
| `_require_session()` / `_require_observation()` | Return current state or raise a reason-coded workflow error instead of using absent/stale state. |
| `_origin_from_url(url)` | Converts a parsed URL to canonical scheme/host/explicit-port origin. Pure helper. |
| `_target_origin(action, current_origin)` | Returns the linked target origin when present; otherwise the current origin. Pure helper for policy review. |
| `_download_filename(url)` | Extracts and URL-decodes only the last path component; falls back to a neutral filename for policy validation. |
| `_declared_mime(filename)` | Maps the accepted extension to its expected MIME value; unknown extensions use a value that policy will reject. |

### `confirmation.browser` and `persistence.browser`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserConfirmationService.__init__(repository, ttl_seconds, now=...)` | Injects durable storage, positive TTL and a UTC clock. Invalid TTL raises `ValueError`. |
| `request(plan)` | Creates a PENDING record bound to plan/action digests, origin, session/page/navigation and expiry; persists it before returning. |
| `resolve(confirmation_id, approved)` | Delegates the exact state transition to storage at the injected current time. |
| `consume(record, plan)` | Requires ID, plan, digests, generation, origin and expiry to match, then atomically transitions APPROVED to CONSUMED. Replay or drift raises `BrowserAuthorityError`. |
| `BrowserConfirmationRepository.__init__(database_path)` | Configures a SQLite/SQLAlchemy repository for one local path; no schema is created yet. |
| `initialize()` | Creates the confirmation table and invalidates PENDING/APPROVED records left by a previous process. Database failure propagates and browser writes remain unavailable. |
| `create(record)` | Inserts one immutable confirmation; duplicate IDs or database errors fail. |
| `resolve(confirmation_id, approved, now)` | In one transaction, requires PENDING and unexpired, then records APPROVED or REJECTED. Unknown, expired or already-resolved authority fails. |
| `consume(record, plan_digest, action_digest, now)` | Executes one conditional SQL update requiring APPROVED, unexpired and every binding equal; exactly one affected row is required. This is the durable replay-protection boundary. |
| `invalidate_session(session_id)` | Atomically marks the session's PENDING/APPROVED rows INVALIDATED and returns the row count. |
| `state(confirmation_id)` | Returns the durable enum state or raises for an unknown ID. |
| `close()` | Disposes database engine resources. |
| `_require_initialized()` | Returns only when engine/session factory exist; otherwise raises fail-closed repository error. |
| `BrowserWriteExecutionGuard.__init__()` | Creates an in-memory lock and empty issued-token set for the current process. |
| `issue(plan_digest, preview_digest)` | Creates one opaque token bound to the exact plan/Preview digests and stores it under lock. It is not durable across restart. |
| `require(token, plan_digest, preview_digest)` | Under lock, requires and removes the exact token. Wrong binding or replay fails; successful validation consumes authority before file commit. |

## Registered tools

### `tools.browser_tools`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `_r0_manifest(...)` | Builds an immutable R0, rollback-NONE, standard-user, cancellable manifest for one finite browser read/navigation action. |
| `BrowserSessionOpenTool.__init__(adapter)` | Injects the worker adapter. |
| `BrowserSessionOpenTool.manifest` | Returns the `browser.session.open` manifest. |
| `BrowserSessionOpenTool.execute(request, cancellation)` | Validates `BrowserSessionOpenInput`, checks cancellation and creates one ephemeral session through the adapter. |
| `BrowserNavigateTool.__init__(adapter)` / `manifest` | Injects the adapter and exposes only `browser.page.navigate`. |
| `BrowserNavigateTool.execute(request, cancellation)` | Validates the explicit URL/HTTP flag, checks cancellation and performs worker navigation. URL safety must already have passed and is repeated by orchestration/worker routing. |
| `BrowserObserveTool.__init__(adapter)` / `manifest` | Injects the adapter and exposes only `browser.page.observe`. |
| `BrowserObserveTool.execute(request, cancellation)` | Validates the session input, checks cancellation and returns a bounded observation. |
| `BrowserElementActionTool.__init__(adapter)` / `manifest` | Injects the adapter and exposes only `browser.element.activate`; the action schema remains closed. |
| `BrowserElementActionTool.execute(request, cancellation)` | Validates a typed semantic action and delegates it. It cannot accept selectors, scripts or arbitrary args. |
| `BrowserDocumentDownloadTool.__init__(adapter, temporary_directory)` / `manifest` | Stores the exact Agent-owned staging root and exposes the only R1 browser writer, `browser.document.download`. |
| `BrowserDocumentDownloadTool.execute(request, cancellation)` | Validates the typed DOWNLOAD_DOCUMENT action, checks cancellation, creates the staging root and asks the worker for one temporary file. It does not commit to the selected user destination. |
| `build_browser_registry(adapter, temporary_directory)` | Registers exactly the five Stage 5C tools and returns the immutable registry. Duplicate names fail through registry validation; there is no generic browser tool. |

Input models (`BrowserSessionOpenInput`, `BrowserNavigateInput`, `BrowserObserveInput`, `BrowserActionInput`,
`BrowserDownloadInput`) are Pydantic schemas that reject extra fields. The output is always one typed domain
model.

## Worker protocol and browser adapter

### `browser.adapter`, `browser.client`, `browser.protocol`, `browser.worker`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserAdapter.start/navigate/observe/perform/download/cancel/close` | Provider-neutral protocol implemented by fake, Playwright and worker client adapters. It fixes the finite operations orchestration may request. |
| `BrowserWorkerClient.__init__(timeout_seconds=60)` | Validates a positive timeout and prepares serialized IPC state. It does not launch a process. |
| `start(headless=False)` | Launches the owned standard-user worker with `sys.executable -I`, no shell, scrubbed environment and pipes, starts the response reader, sends START and returns a typed session. |
| `navigate(url, allow_http=False)` / `observe()` | Send one finite command and parse an observation; timeout/protocol/error response raises `BrowserWorkerError`. |
| `perform(action)` | Sends canonical typed action JSON and requires an ACTION_RESULT payload before Pydantic parsing. |
| `download(action, temporary_directory)` | Resolves an existing Agent staging root, sends the finite DOWNLOAD command and requires a typed worker artifact response. |
| `cancel()` | Sends CANCEL; if IPC cannot complete, terminates only the owned worker. |
| `close()` | Requests CLOSE, waits up to five seconds, then terminates only the owned worker if necessary. Idempotent at client level. |
| `__enter__()` / `__exit__(...)` | Context-manager support; exit always closes the owned worker. |
| `_exchange(request)` | Serializes commands with a lock, writes one JSON line, waits for one response, checks request ID/success and returns the typed envelope. Pipe/timeout failure terminates the owned worker and raises. |
| `_read_responses()` | Reader-thread loop that queues stdout lines and a terminal sentinel. It never parses or executes page text. |
| `_terminate_owned_worker()` | Gracefully terminates then kills only the stored child after timeout. No process-name search. |
| `_close_pipes()` | After the owned child exits, joins the response reader briefly and closes parent stdin/stdout handles so shutdown does not leak Windows handles. |
| `_parse_session(response)` / `_parse_observation(response)` | Require the exact payload discriminator and non-null JSON before Pydantic parsing. |
| `_worker_environment()` | Copies the current environment while dropping names containing credential/token/API/cookie markers. It does not expose the removed values. |
| `BrowserWorkerRequest.enforce_command_shape()` | Rejects fields not belonging to the selected command so JSON-lines IPC cannot evolve into a generic runner accidentally. |
| `BrowserWorkerResponse.require_matching_payload()` | Requires NONE with no JSON, or a non-NONE discriminator with JSON. Ambiguous responses are invalid. |
| `run_worker()` | Creates one Playwright adapter, processes stdin sequentially, validates every request, dispatches only the allow-list, emits one response line and closes on EOF/CLOSE. Exception details are deliberately not reflected. Returns a process exit code. |
| `_dispatch(adapter, request)` | Exhaustively maps START/NAVIGATE/OBSERVE/PERFORM/DOWNLOAD/CANCEL/CLOSE to the matching adapter method and typed payload. Unknown commands raise; there is no reflection or dynamic invocation. |
| `_request_id_or_zero(line)` | Recovers only a valid request ID for error correlation; malformed input returns all-zero UUID without reflecting the input. |

### `browser.playwright_adapter`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `PlaywrightBrowserAdapter.__init__(settings=None, url_policy=None, injection_detector=None, route_fulfiller=None)` | Injects bounds and policies. `route_fulfiller` exists only for synthetic tests; the runtime composition never supplies it. No browser starts yet. |
| `start(headless=False)` | Starts managed Chromium, creates a non-persistent context with service workers blocked, HTTPS errors enforced and browser sandbox requested, installs request routing and opens one page. Returns ACTIVE session evidence. |
| `navigate(url, allow_http=False)` | Fresh-validates the URL, performs a bounded navigation, verifies the final redirect URL and returns a new bounded observation. Navigation errors propagate as failure. |
| `observe()` | Reads title, bounded visible body text and finite accessibility-role locators; constructs selector-free references and prompt-injection signals. Limits are enforced before returning. |
| `perform(action)` | Revalidates generation and exact semantic locator, implements only the closed back/forward/reload/link/search/filter/pagination/expand/takeover actions and returns an observation-backed result. Transactional actions are not implemented. |
| `download(action, temporary_directory)` | Requires DOWNLOAD_DOCUMENT and a fresh link reference, waits for exactly one Playwright download, saves to a random temporary filename beneath the supplied exact root and returns staging evidence. |
| `cancel()` | Marks cancellation and closes the owned page/context; it does not terminate other browsers. |
| `close()` | Closes page, context, browser and Playwright objects in ownership order and marks the session closed. |
| `_route_request(route)` | While Agent-controlled, allows only GET/HEAD after fresh URL/DNS validation and aborts blocked methods/schemes/hosts; during explicit takeover it lets the user interact. Test fixtures may fulfill synthetic routes. |
| `_fresh_locator(reference)` | Reconstructs a locator solely from allow-listed accessibility role/name and requires exactly one match. Stale/ambiguous elements fail. |
| `_validate_generation(action, session_id)` | Requires action/session/page/navigation IDs to match current state before any action. |
| `_observe_after_action()` | Waits for bounded DOM settling and obtains a new observation; it does not assume a click succeeded. |
| `_http_is_confirmed(url)` | Returns true only for HTTPS or the single explicitly confirmed HTTP origin recorded for current navigation. |
| `_require_active()` / `_require_session()` | Return owned objects and descriptor only in the permitted lifecycle state; otherwise raise. |
| `_role_locator(page, role, name=None)` | Maps the finite role enum to Playwright `get_by_role`; unsupported roles cannot become selectors. |
| `_accessible_name(locator)` | Obtains and trims a bounded accessibility label using permitted attributes/text fallback. It never treats the value as an instruction. |
| `_sanitized_child_environment()` | Produces the fixed Chromium launch environment/options without provider credentials or generic user-supplied data. |

`FakeBrowserAdapter` mirrors `start`, `navigate`, `observe`, `perform`, `download`, `cancel` and `close` for
tests. `_require_active()` and `_require_action_session()` enforce lifecycle and identity. The fake never
contacts a network and cannot be used as production evidence.

## Optional model provider

### `providers.llm.browser` and `providers.llm.openai_browser`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `BrowserContentProvider.destination` | Protocol property describing the external destination shown to consent/audit code. |
| `BrowserContentProvider.summarize(request)` | Async protocol for a minimized, explicitly consented, untrusted-content summary. It returns advisory text only. |
| `OpenAIBrowserContentProvider.__init__(model, api_key, client=None)` | Stores model and an injected or official async client. The API key is not logged or persisted by this class. |
| `destination` | Returns the fixed OpenAI destination label for consent UI. |
| `summarize(request)` | Envelopes untrusted bounded sections, calls the Responses API with no tools and no storage, parses strict JSON into `BrowserModelSummary`, and returns advice with no action authority. Provider/network/JSON errors propagate; no automatic retry occurs. |

Model summarization is disabled by default in the Stage 5C runtime. Enabling it in a future UI requires a
separate external-data disclosure confirmation; browser login state, cookies, secrets and full page bodies
must not be sent.

## Audit API

### `audit.browser.BrowserAuditLogger`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `__init__(repository, git_commit=None)` | Injects structured audit storage and optional source revision. |
| `plan_reviewed(plan)` | Records plan/action digests, redacted origin, risk, decision and finite action kind; it does not record page text, query values or destination paths. |
| `confirmation_resolved(plan, approved)` | Records the exact plan ID/digest and yes/no outcome. |
| `observation(observation)` | Records origin, counts, truncation and injection-signal enums only; no visible text or accessible names. |
| `action_completed(plan, result)` | Records typed completion/reason and post-navigation origin; no raw URL query or page body. |
| `download_completed(preview, identity, recovery)` | Records source origin, Preview/download IDs, size, SHA-256, MIME and recovery level. It omits local path and filename. |
| `download_rolled_back(record)` | Records operation/download IDs, SHA-256 and recovery truth without exposing either path. |

Audit repository failure propagates. Stage 5C does not continue a confirmed write when mandatory durable
authority or audit storage is unavailable.

## Qt UI and routing

### `ui.browser_tab`

| Function or method | Purpose, inputs, output, failure and side effects |
|---|---|
| `_BrowserWorker.__init__(operation)` / `run()` | Wraps one orchestration callable in `QRunnable`; `run()` emits result or a user-safe error string so long browser work does not block Qt's UI thread. It never bypasses orchestration. |
| `BrowserTab.__init__(services, parent=None)` | Stores composed services and creates empty session/plan/download/UI state. |
| `_build_ui()` | Builds goal/URL controls, exact plan/confirmation controls, semantic element table, download destination, takeover, Office handoff, progress and status widgets. |
| `set_user_goal(text)` | Places routed chat/voice text into the visible goal field; it does not submit or execute it. |
| `_start_session()` / `_session_started(value)` | Starts the orchestration service in the thread pool and updates controls from typed session evidence. |
| `_plan_navigation()` | Validates visible goal/URL presence and asynchronously prepares a plan. No navigation occurs. |
| `_plan_element_action()` | Maps the selected finite UI action and element row to a typed plan request. Invalid/missing selection is reported without execution. |
| `_plan_download()` | Requires selected link and destination, then asks orchestration for an R1 Preview/plan pair. |
| `_plan_ready(value)` / `_download_plan_ready(value)` | Type-check and display exact action, origin, risk, expected effect, rollback and download impact; store only the current pending objects. |
| `_approve_plan()` / `_reject_plan()` | Create and resolve the durable confirmation for the exact displayed plan. Reject clears pending authority. |
| `_execute_plan()` | Requires an approved current confirmation, then asynchronously invokes either `execute` or `execute_download`. It cannot execute from free-form UI values. |
| `_action_completed(value)` / `_download_completed(value)` | Type-check results, update bounded observation/status and remember the latest recovery/handoff evidence. |
| `_show_observation(observation)` | Renders redacted/bounded page status and semantic rows; remote text remains display data, not UI commands. |
| `_choose_download_directory()` | Opens a directory picker and records user intent only. |
| `_begin_takeover()` / `_takeover_started(value)` | Invalidate pending authority and enter user-controlled mode; UI explains that handback will re-observe. |
| `_end_takeover()` / `_handback_completed(value)` | End takeover in the background, display the fresh generation and re-enable planning. |
| `_handoff_to_office()` | Emits only the validated downloaded path as a suggestion. Stage 5A must separately select, inspect, plan and confirm it. |
| `_rollback_last_download()` / `_rollback_completed(value)` | Requests conditional recovery and reports the moved recovery location; changed/conflicting files fail closed. |
| `_selected_element_id()` | Returns the UUID stored in the selected semantic row or `None`; it never parses a selector. |
| `_clear_pending(invalidate=True)` | Optionally invalidates all session approvals, clears current plan/Preview/confirmation and updates controls. Storage failure is shown and does not silently preserve authority. |
| `_run(operation, completed)` / `_finished(value, completed)` | Submit one runnable, manage progress/button state and invoke the typed completion callback. |
| `_worker_failed(message)` / `_failure(message)` | Clear busy state and present a bounded friendly failure; no retry or fallback executes. |
| `cancel()` | Cancels current browser work through orchestration and clears pending state. |
| `shutdown()` | Calls service close during application shutdown and returns whether cleanup completed. |

`MainWindow` routes browser-like chat/voice input to `BrowserTab.set_user_goal`, includes the Browser tab in
active-domain selection, calls `BrowserTab.shutdown()` on controlled exit, and connects the download hint to
`OfficeAutomationTab.suggest_downloaded_document(path)`. That Office method only displays a suggestion.

## Installation and verification

Install the managed Chromium runtime once after dependency synchronization:

```powershell
uv sync --all-groups
uv run playwright install chromium
```

Useful focused checks:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/browser tests/integration/browser tests/security/test_browser_boundaries.py tests/gui/test_browser_tab.py -q
uv run pytest tests/integration/browser/test_playwright_contract.py -q
```

The Playwright contract uses a synthetic local route fixture. It must not access private-network or real
user sites and does not prove that every external site is compatible.
