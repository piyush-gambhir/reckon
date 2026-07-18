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
