# On-call and escalation

Who to contact for what. Keep this concise — names and Slack handles only, no full org chart.

## Format

For each team / domain:
- **Primary on-call**: who's paged today (check a rotation tool if needed)
- **Team Slack channel**: where to ask questions
- **Incident channel**: where active incidents go

## Teams

### Media / 360
- Primary on-call: `<tbd>`
- Team channel: `#<tbd>`
- Services: `MEDIA-SERVICE`, `360-BACKEND`, `MEDIA-HANDLER`, `MEDIA-CONFIG`

### Inventory / Dealer
- Primary on-call: `<tbd>`
- Team channel: `#<tbd>`
- Services: `INVENTORY-APIS`, `DEALER-*`

### Platform (DB / infra)
- Primary on-call: `<tbd>`
- Team channel: `#<tbd>`
- DB issues, reverse proxy changes, CubeAPM server config

### SRE / on-call rotation
- Primary on-call: `<tbd>`
- Incident channel: `#<tbd>`

## Communication template

When an RCA is being actively investigated:

1. Post the start of investigation in the incident channel with the service, window, and initial symptom.
2. Update roughly every 10 minutes with the current hypothesis.
3. Post the final RCA link when done.

(None of this is required for post-hoc analysis of a resolved incident — skip it.)
