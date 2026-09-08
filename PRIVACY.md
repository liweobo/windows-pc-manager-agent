# Privacy

The Agent stores operational state locally and has no telemetry implementation. Logs and crash
reports are bounded and redacted; diagnostic bundles require explicit review and are never uploaded
automatically. Cloud LLM or speech providers receive data only through their separate, user-reviewed
flows. Browser and Office content remain untrusted and do not grant execution authority.

The complete data-flow, persistence, retention and user-control matrix is in
[`docs/release/privacy.md`](docs/release/privacy.md). Redaction is defense in depth and is not a
guarantee that every sensitive value can be recognized; review exports before sharing them.
