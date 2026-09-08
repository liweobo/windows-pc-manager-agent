# Release privacy review

| Data | Local persistence | Provider transmission | Retention | User control |
|---|---|---|---|---|
| File/system metadata | Report/Audit fields within approved scope | Only via separately reviewed provider flow | Until user clears local data | Choose roots, exclusions and reports |
| LLM prompt/context | Not stored as raw production log content | Only when provider is configured and request is submitted | Provider policy applies | Provider can remain disabled |
| Voice PCM/transcript | PCM and transcript bodies are memory-only | Each cloud STT/TTS disclosure is separately confirmed | No Agent body retention | Review text; cancel/disable Voice |
| Browser content | Ephemeral session; no normal profile/cookies | Website receives normal navigation traffic | Session ends on close | Browser disabled in private RC |
| Office body/Diff | Volatile; output/backup only where explicitly chosen | Exact selected spans need independent disclosure | User controls files/backups | Office disabled in private RC |
| Memory | Closed low-risk keys only | Never used as execution authority | TTL/version policy; physical delete controls | View/edit/delete/disable |
| Audit | IDs, digests, risk, decisions and results; content excluded | Never automatically uploaded | Indefinite in Stage 7A | Local review; future scoped reset |
| Logs/crash | Rotating redacted JSONL and ten sanitized reports | Never automatically uploaded | 2 MiB x 6 logs; ten crash reports | Reviewed local diagnostic export |

Telemetry is `NOT_IMPLEMENTED`. Diagnostic ZIP creation is R1, defaults to No, lists exact sanitized
members and exclusions, uses single-use authority and does not upload the result. Known-pattern
redaction cannot recognize every possible sensitive value; users must review a bundle before sharing.
