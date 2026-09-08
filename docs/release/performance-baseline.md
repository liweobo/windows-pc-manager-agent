# Stage 7A performance baseline

Measured on 2026-09-08 with Python 3.13.1 on the Windows 11 x64 development host. The command was
`uv run pytest tests/performance -q -s`; all 12 synthetic tests passed in 206.47 seconds. Results are a
regression baseline, not a guarantee for other hardware, disks, document complexity or live websites.

| Scenario | Workload | Wall time | Python traced peak | Enforced limit |
|---|---:|---:|---:|---:|
| Browser bounded observation | 5,000 semantic elements and 40,000 visible characters | 0.894 s | 6.88 MiB | <15 s, <256 MiB |
| R0 file scan | 10,000 synthetic files | 43.888 s | 1.98 MiB | <120 s, <256 MiB |
| Office CSV parse | 10,000 synthetic rows | 0.517 s | 21.05 MiB | <30 s, <256 MiB |
| Office XLSX parse | 10,000 synthetic rows | 3.732 s | 36.64 MiB | <30 s, <256 MiB |
| Office PDF parse | 100 synthetic blank pages | 0.169 s | 0.98 MiB | <30 s, <256 MiB |
| Final Orchestrator lifecycle | 100 create-and-cancel operations plus reload | 4.263 s | 0.67 MiB | <30 s, <128 MiB |
| Stage 4D4 Fresh residual scan | 5,001 synthetic objects | 64.908 s | 93.42 MiB | <120 s, <256 MiB |
| Stage 4D3 metadata residual scan | 10,001 synthetic objects | 72.735 s | 113.40 MiB | <180 s, <256 MiB |

The system-diagnostics collector and other non-printing performance cases also passed their coded bounds. Installed
cold/warm startup, idle CPU/RAM, tray interaction, real-disk antivirus effects and long-lived leak measurements remain
manual `NOT_RUN` items in the release matrix. Frozen Main smoke initialization completed in 6.992 seconds with an
isolated temporary database; this is a functional smoke measurement, not a cold-start benchmark.
