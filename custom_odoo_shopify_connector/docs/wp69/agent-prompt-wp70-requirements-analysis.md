# Agent prompt — Analyze OpenProject WP #70 + Excel → Requirements analysis

Copy the block below to your **local master machine** agent (where OpenProject is reachable).

---

```
Analyze OpenProject work package #70 and its Excel attachment, then write or update the requirements analysis markdown file.

## Goal
Produce a complete requirements analysis at:
  /opt/odoo-log-report-2026-06-28/docs/requirements-analysis-wp70.md

## Credentials (environment variables)
- OPENPROJECT_URL          — e.g. https://openproject.example.com
- OPENPROJECT_API_KEY      — personal access token
- OPENPROJECT_WP_ID=70     — optional, default 70

## Steps

### 1. Fetch work package #70
curl -s -u "apikey:$OPENPROJECT_API_KEY" \
  "$OPENPROJECT_URL/api/v3/work_packages/70" | jq '{id, subject, description: .description.raw, status: ._links.status.title, project: ._links.project.title, type: ._links.type.title}'

### 2. List and download attachments
curl -s -u "apikey:$OPENPROJECT_API_KEY" \
  "$OPENPROJECT_URL/api/v3/work_packages/70/attachments" | jq '._embedded.elements[] | {id, title, fileName, download: ._links.download.href}'

Download each attachment (especially .xlsx / .xls) to:
  /opt/odoo-log-report-2026-06-28/docs/attachments/

### 3. Parse Excel
Use openpyxl or pandas. Extract columns such as:
- Req ID / ID
- Module / Area
- Requirement / Title / Description
- Priority / MoSCoW
- Status / State
- Notes / Comments

### 4. Cross-reference with Odoo remediation (already on server)
Read these files for implementation evidence:
- /opt/odoo-log-report-2026-06-28/FINAL_REMEDIATION_REPORT.md
- /opt/odoo-log-report-2026-06-28/odoo-log-analysis-report.md
- /opt/odoo-log-report-2026-06-28/baseline/PHASE*.md

Map each Excel requirement to:
- Met / Partial / Not met / Out of scope
- Evidence (commit, phase, validation result)

### 5. Or run the provided script
python3 /opt/odoo-log-report-2026-06-28/docs/fetch-wp70-and-analyze.py

Then manually enrich Section 5–7 if Excel columns don't match script heuristics.

### 6. Update requirements-analysis-wp70.md
Ensure the document includes:
1. Purpose & scope
2. WP #70 metadata (subject, status, description)
3. Excel requirements table (all rows)
4. Traceability matrix: Excel req → Odoo/Shopify delivery
5. Gap analysis & recommendations
6. Sign-off checklist

### 7. Return
- Path to updated MD file
- Count of Excel requirements
- Count Met / Partial / Not met
- OpenProject WP URL

Do not print the API key.
```

## Quick one-liner

```
Fetch OpenProject WP 70 + Excel attachment, parse requirements, cross-check against FINAL_REMEDIATION_REPORT.md and odoo-log-analysis-report.md, update /opt/odoo-log-report-2026-06-28/docs/requirements-analysis-wp70.md with full traceability matrix. Use OPENPROJECT_URL and OPENPROJECT_API_KEY from env.
```
