# Windows PC Manager Agent — V1 Scope Definition

**Version:** 1.0  
**Date:** 2026-09-12  
**Status:** PROPOSED (awaiting stakeholder approval)  
**Authority:** Product requirements document for V1.0 release

---

## Purpose

This document defines the **authoritative feature scope for Windows V1.0 release**.

All feature enablement decisions MUST reference this document.

---

## V1 Scope Decision: Option B (Pragmatic V1) ⭐

### Included in V1.0

#### R0 Domains (Read-Only Analysis) — 4 domains

| Domain | Description | Status |
|--------|-------------|--------|
| **File Analysis** | Scan directories, analyze file sizes/types/duplicates, generate reports | ✅ ENABLED in Private RC |
| **System Diagnostics** | Collect system info (OS, hardware, disk, network) | ✅ ENABLED in Private RC |
| **Software Analysis** | List installed applications, identify update candidates | ✅ ENABLED in Private RC |
| **System Optimization Analysis** | Analyze startup items, services, scheduled tasks (read-only) | ✅ ENABLED in Private RC |

#### R1-R2 Action Domains (WITH Confirmation) — 4 domains

| Domain | Description | Risk Level | Status |
|--------|-------------|-----------|--------|
| **File Operations** | Move, rename, organize files with two-level confirmation | R2 | 🔒 DISABLED (enable after manual testing) |
| **Recycle Bin Management** | Send files to/restore from Windows Recycle Bin | R2 | 🔒 DISABLED (enable after manual testing) |
| **Software Uninstall (Execution)** | Execute vendor uninstallers for selected applications | R2 | 🔒 DISABLED (enable after manual testing) |
| **Residual Analysis** | Detect leftover files/registry after uninstall (read-only scan) | R1 | 🔒 DISABLED (enable after manual testing) |

**Total V1 Scope: 8 domains**

---

## Explicitly Deferred to V2+

### Medium-Risk Action Domains — 5 domains

| Domain | Reason Deferred | Target Version |
|--------|----------------|----------------|
| **Process Actions** | Can break UI/system if critical process terminated | V2 |
| **Startup Management** | Registry writes, boot risk if misconfigured | V2 |
| **Service Management** | Critical services can break Windows if disabled | V2 |
| **Residual Cleanup** | Depends on Residual Analysis accuracy validation | V2 |
| **System Cleanup** | Lower priority, temp file cleanup less impactful | V2 |

### Not Production Ready — 1 domain

| Domain | Reason Deferred | Target Version |
|--------|----------------|----------------|
| **Privileged Broker** | Requires: code signing, real UAC validation, external security audit | V2 (4-6 weeks infrastructure work) |

### Experimental / Infrastructure — 6 domains

| Domain | Reason Deferred | Target Version |
|--------|----------------|----------------|
| **Office Integration** | Real Office/WPS not validated | V3+ |
| **Voice Control** | Real audio devices not validated | V3+ |
| **Browser Integration** | Test failures, real websites not validated | V3+ |
| **Memory Management** | Concept only, not production-ready | V3+ |
| **Multi-Agent Coordination** | Infrastructure, not user-facing feature | N/A (internal) |
| **Final Orchestrator** | Recovery mechanism, not standalone feature | N/A (internal) |

---

## V1 Enablement Criteria

### Prerequisites for Enabling R2 Action Domains

Each of the 4 action domains (File Operations, Recycle Bin, Software Uninstall, Residual Analysis) MUST complete:

#### 1. Manual Testing (REQUIRED)

- [ ] **Test plan documented** in `docs/release/manual-test-matrix-v1.md`
- [ ] **Happy path testing:** 10+ successful operations
- [ ] **Edge case testing:** Permission denied, disk full, path too long, special characters
- [ ] **Error handling:** Graceful failures documented
- [ ] **Confirmation flow:** Two-level confirmation validated with real user
- [ ] **Recovery mechanism:** Interruption/rollback tested
- [ ] **Test evidence recorded:** Screenshots, logs, success/failure counts

#### 2. Code Signing (REQUIRED for ALL domains)

- [ ] **Certificate procured:** Authenticode code signing certificate obtained
- [ ] **Build integration:** CI pipeline signs Main, Broker, Installer
- [ ] **Signature validation:** Signed binaries verified with `signtool`
- [ ] **SmartScreen:** Signed installer tested on clean Windows 11 VM

#### 3. Documentation (REQUIRED)

- [ ] **User documentation:** Feature usage guide created
- [ ] **Known limitations:** Edge cases and unsupported scenarios documented
- [ ] **Release notes:** Feature description added to V1 release notes
- [ ] **Error messages:** User-facing errors are clear and actionable

#### 4. Feature Flag Update (FINAL STEP)

- [ ] **Production config:** Update `src/pc_manager_agent/config/production.py` to enable domain
- [ ] **Smoke test:** Run frozen smoke tests with new configuration
- [ ] **CI validation:** Push to branch, verify CI passes with enabled feature

### Quality Gates (Must remain GREEN)

- ✅ All automated tests pass
- ✅ Code coverage ≥ 85%
- ✅ Security scans clean
- ✅ Build succeeds
- ✅ Installer lifecycle works

---

## V1 Timeline

### Phase 1: Manual Testing (Weeks 1-2)

| Domain | Estimated Work | Assignee | Status |
|--------|---------------|----------|--------|
| File Operations | 2-3 days | TBD | NOT_STARTED |
| Recycle Bin | 1-2 days | TBD | NOT_STARTED |
| Software Uninstall | 2-3 days | TBD | NOT_STARTED |
| Residual Analysis | 1-2 days | TBD | NOT_STARTED |

**Total: 6-10 days** (can be parallelized)

### Phase 2: Code Signing (Weeks 2-3)

| Task | Estimated Work | Assignee | Status |
|------|---------------|----------|--------|
| Procure certificate | 3-5 days (external) | TBD | NOT_STARTED |
| Integrate into CI | 1-2 days | TBD | NOT_STARTED |
| Validate signatures | 1 day | TBD | NOT_STARTED |

**Total: 1-2 weeks** (includes external dependency)

### Phase 3: V1 Release Candidate (Week 4)

| Task | Estimated Work | Assignee | Status |
|------|---------------|----------|--------|
| Enable validated features | 1 day | TBD | NOT_STARTED |
| Build signed RC | 0.5 day | TBD | NOT_STARTED |
| Final smoke test | 0.5 day | TBD | NOT_STARTED |
| Create release tag | 0.1 day | TBD | NOT_STARTED |

**V1 Target Date: 3-4 weeks from approval**

---

## Success Criteria for V1

### User Value

- ✅ User can **analyze** their PC (files, software, system)
- ✅ User can **organize files** (move, rename, group)
- ✅ User can **uninstall software** (via vendor uninstaller)
- ✅ User can **manage Recycle Bin** (send to / restore from)
- ✅ User can **detect residual files** after uninstall

### Safety

- ✅ **No permanent delete** (only Recycle Bin)
- ✅ **Two-level confirmation** on all R2 operations
- ✅ **TOCTOU protection** (revalidate identity before execution)
- ✅ **Recovery mechanism** (operation journal for rollback)
- ✅ **Graceful errors** (no crashes on permission denied, disk full, etc.)

### Quality

- ✅ **All automated tests pass**
- ✅ **Code coverage ≥ 85%**
- ✅ **Security scans clean**
- ✅ **Manual testing documented**
- ✅ **Code signed** (SmartScreen bypass)

### Distribution

- ✅ **Signed installer** built and tested
- ✅ **Installation works** on clean Windows 11 VM
- ✅ **Uninstall is clean** (no leftover files)
- ✅ **User documentation** available

---

## Non-Goals for V1

### What V1 Will NOT Do

- ❌ **Manage processes** (kill/suspend) — deferred to V2
- ❌ **Manage startup items** — deferred to V2
- ❌ **Manage Windows Services** — deferred to V2
- ❌ **Clean residual files** (beyond detection) — deferred to V2
- ❌ **Clean temp files** — deferred to V2
- ❌ **Require elevated privileges** (Broker disabled) — deferred to V2
- ❌ **Integrate with Office/Voice/Browser** — deferred to V3+
- ❌ **Support Linux/macOS** — Windows-only
- ❌ **Offer silent/unattended mode** — interactive confirmation required

---

## Risk Assessment

### V1 Risk Level: 🟢 LOW

**Rationale:**
1. All R2 domains require two-level confirmation
2. No permanent delete (Recycle Bin only)
3. TOCTOU protection prevents race conditions
4. Recovery mechanism for interrupted operations
5. No service/process management (high-risk domains deferred)
6. No elevated privileges required (Broker disabled)

### Residual Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| File operation bug deletes wrong files | LOW | HIGH | Recycle Bin only, two-level confirmation, manual testing |
| Confusing confirmation UX | MEDIUM | LOW | Manual testing will validate, user feedback loop |
| Network drive compatibility issues | MEDIUM | MEDIUM | Test on SMB shares, document limitations |
| Vendor uninstaller fails | LOW | MEDIUM | Error handling, document known issues |

---

## Approval & Governance

### Approvers

- [ ] **Product Owner:** _________________ (Date: ______)
- [ ] **Engineering Lead:** _________________ (Date: ______)
- [ ] **QA Lead:** _________________ (Date: ______)

### Change Control

**Any changes to this V1 scope require approval from all three roles above.**

**Process for scope changes:**
1. Propose change with rationale (why add/remove domain)
2. Assess impact (timeline, risk, testing effort)
3. Get approval from Product Owner, Engineering Lead, QA Lead
4. Update this document with version bump and change log

### Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 (PROPOSED) | 2026-09-12 | Claude Opus 5 | Initial V1 scope definition (Option B: Pragmatic V1) |

---

## References

- **Wave 2 Audit:** `PHASE2_WAVE2_V1_SCOPE_AUDIT.md` (full analysis)
- **Executive Summary:** `PHASE2_WAVE2_EXECUTIVE_SUMMARY.md` (decision rationale)
- **Private RC Scope:** `docs/release/readiness-assessment.md` (Section A)
- **Capability Matrix:** `AUDIT_REPORT_V1.0_BASELINE.md` (Section 3)

---

**END OF V1 SCOPE DEFINITION**

**This document is the authoritative source for V1 feature scope.**  
**All enablement work MUST reference this document.**  
**Changes require formal approval.**
