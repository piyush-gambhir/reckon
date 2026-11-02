# Security policy

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Use GitHub's
**Report a vulnerability** option in the repository Security tab so the report
and any supporting evidence remain private until a fix is available.

Include the affected component, reproduction steps, expected impact, and any
suggested mitigation. You should receive an acknowledgement within seven days.

## Operational safety

Reckon connects to production infrastructure. Its client-side restrictions are
defence in depth, not an authorization boundary. Always use server-side
read-only database roles, read-only cloud and observability credentials, and
read-only Kafka ACLs as described in the project documentation.

## CodeQL Rust logging triage (2026-10-29)

The seven `rust/cleartext-logging` findings on `src/cli.rs` and
`src/onepassword.rs` propagate secret taint across larger result structures.
The specific flagged fields print connection names, execution status, cache
observation time, quota notes, action previews, incident counts, collection
counts, or credential setting names. Setting names in the confirmation prompt
are not their values. The subprocess stdout and stderr immediately beside the
flagged run metadata are separately passed through `orgs::redact` with the
resolved secret values before output.

These locations require field-level review, not removal of redaction or a
blanket logging exclusion. Preserve the existing redaction path. Do not add raw
credential values, unresolved provider output, or secret-containing command
parameters to metadata messages. The static findings are documented; they are
not treated as proof that every possible log or preview is free of private data.
