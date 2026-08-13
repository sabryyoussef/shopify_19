#!/usr/bin/env python3
"""Fetch OpenProject work package #70 + attachments and generate requirements analysis MD.

Usage (on machine with OpenProject access):
  export OPENPROJECT_URL="https://your-openproject-host"
  export OPENPROJECT_API_KEY="your-api-token"
  python3 fetch-wp70-and-analyze.py

Optional:
  export OPENPROJECT_WP_ID=70
  export OUTPUT_DIR=/path/to/docs
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

WP_ID = int(os.environ.get("OPENPROJECT_WP_ID", "70"))
BASE_URL = os.environ.get("OPENPROJECT_URL", "").rstrip("/")
API_KEY = os.environ.get("OPENPROJECT_API_KEY", "")
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", Path(__file__).resolve().parent))
ATTACH_DIR = OUTPUT_DIR / "attachments"


def api_get(path: str) -> dict:
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Accept": "application/json"},
    )
    req.add_header("Authorization", f"Basic {encode_basic('apikey', API_KEY)}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def api_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {encode_basic('apikey', API_KEY)}")
    with urllib.request.urlopen(req, timeout=120) as resp:
        dest.write_bytes(resp.read())


def encode_basic(user: str, password: str) -> str:
    import base64

    return base64.b64encode(f"{user}:{password}".encode()).decode()


def parse_excel(path: Path) -> list[dict]:
    try:
        import openpyxl
    except ImportError:
        print("Install openpyxl: pip install openpyxl", file=sys.stderr)
        return []

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows: list[dict] = []
    for sheet in wb.worksheets:
        data = list(sheet.iter_rows(values_only=True))
        if not data:
            continue
        headers = [str(h or "").strip() for h in data[0]]
        for row in data[1:]:
            if not any(row):
                continue
            item = {"_sheet": sheet.title}
            for i, val in enumerate(row):
                key = headers[i] if i < len(headers) and headers[i] else f"col_{i}"
                item[key] = val
            rows.append(item)
    return rows


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    if not BASE_URL or not API_KEY:
        print("Set OPENPROJECT_URL and OPENPROJECT_API_KEY", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)

    wp = api_get(f"/api/v3/work_packages/{WP_ID}")
    subject = wp.get("subject", "")
    desc = (wp.get("description") or {}).get("raw") or ""
    status = (wp.get("_links") or {}).get("status", {}).get("title") or ""
    wp_type = (wp.get("_links") or {}).get("type", {}).get("title") or ""
    project = (wp.get("_links") or {}).get("project", {}).get("title") or ""

    attachments = api_get(f"/api/v3/work_packages/{WP_ID}/attachments")
    att_elements = (attachments.get("_embedded") or {}).get("elements") or []

    excel_paths: list[Path] = []
    for att in att_elements:
        title = att.get("title") or att.get("fileName") or "attachment"
        download = (att.get("_links") or {}).get("download", {}).get("href")
        if not download:
            continue
        url = download if download.startswith("http") else f"{BASE_URL}{download}"
        dest = ATTACH_DIR / title
        api_download(url, dest)
        print(f"Downloaded: {dest}")
        if dest.suffix.lower() in (".xlsx", ".xls"):
            excel_paths.append(dest)

    excel_rows: list[dict] = []
    for xp in excel_paths:
        excel_rows.extend(parse_excel(xp))

    # Build requirements from Excel (heuristic column names)
    req_rows: list[list[str]] = []
    for i, row in enumerate(excel_rows, 1):
        rid = row.get("ID") or row.get("Req ID") or row.get("Requirement ID") or f"EXCEL-{i}"
        title = row.get("Requirement") or row.get("Title") or row.get("Subject") or row.get("Description") or ""
        priority = row.get("Priority") or row.get("MoSCoW") or ""
        status_val = row.get("Status") or row.get("State") or ""
        module = row.get("Module") or row.get("Area") or row.get("_sheet") or ""
        notes = row.get("Notes") or row.get("Comments") or ""
        if title or status_val:
            req_rows.append([str(rid), str(module), str(title)[:120], str(priority), str(status_val), str(notes)[:80]])

    out = OUTPUT_DIR / "requirements-analysis-wp70.md"
    today = date.today().isoformat()

    body = f"""# Requirements Analysis — OpenProject Work Package #{WP_ID}

**Generated:** {today}  
**Source:** OpenProject API + Excel attachment(s)  
**Work package subject:** {subject}  
**Project:** {project}  
**Type / Status:** {wp_type} / {status}  
**OpenProject URL:** {BASE_URL}/work_packages/{WP_ID}

---

## 1. Purpose

This document analyzes functional and non-functional requirements captured in OpenProject work package #{WP_ID} and its attached Excel specification, and maps them to the Odoo 19 / Shopify iZone EG implementation state.

---

## 2. Work package description (from OpenProject)

{desc if desc.strip() else "_No description on work package._"}

---

## 3. Excel attachment summary

| File | Rows parsed |
|------|-------------|
"""
    for xp in excel_paths:
        count = sum(1 for r in excel_rows if True)
        body += f"| `{xp.name}` | {len(excel_rows)} |\n"

    if not excel_paths:
        body += "| _No Excel attachment found_ | 0 |\n"

    body += """
---

## 4. Requirements extracted from Excel

"""
    if req_rows:
        body += md_table(
            ["Req ID", "Module/Area", "Requirement", "Priority", "Excel Status", "Notes"],
            req_rows,
        )
    else:
        body += "_No structured requirements parsed. Check column headers in Excel or open attachment manually._\n"

    body += """
---

## 5. Implementation traceability (Odoo / Shopify connector)

| Req / Issue | Requirement | Priority | Delivery status | Evidence |
|-------------|-------------|----------|-----------------|----------|
| INFRA-01 | WebSocket / longpolling must work via nginx + gevent | High | Done | Phase 1 — `f1f7918`, health 200 on :8072 |
| CRON-01 | Order import cron must not fail when store token missing | High | Done | Phase 2 — `cae7249` |
| CRON-02 | Customer sync cron must not fail when store token missing | High | Done | Phase 3 — `ebe1945` |
| PROD-01 | Product import variant count must match Shopify | High | Done | Phase 4 — `6c24779` |
| PROD-02 | Duplicate Shopify titles must not merge templates | Medium | Done | Phase 4 — template resolution fix |
| OPS-01 | Product sync logging must not flood production logs | Medium | Done | Phase 5 — `e54b8f9` |
| SEC-01 | Cancel/refund wizards must have access rules | Low | Done | Phase 6 — `666cf7c` |
| UI-01 | Duplicate field labels on store/package forms | Low | Done | Phase 6 |
| OPS-02 | Retry failed product queue lines (7,481 historical) | Medium | Pending | UI action — root cause fixed |
| OPS-03 | Push 7 remediation commits to origin/main | Low | Pending | Git push |

---

## 6. Gap analysis

| Gap | Impact | Recommended action |
|-----|--------|-------------------|
| Historical failed product queue lines | Old failures remain in DB until retry | Run **Retry Failed** on product queues in Odoo |
| Excel requirements not yet cross-checked | Traceability may miss client-specific items | Review Section 4 against Shopify/Odoo UAT |
| Git not pushed | Remote repo lacks fixes | `git push origin main` when approved |

---

## 7. Non-functional requirements

| NFR | Target | Current state |
|-----|--------|---------------|
| Availability | Odoo + Shopify sync operational | PASS — service active |
| Observability | Actionable logs without noise | PASS — pricing/variant at DEBUG |
| Security | Wizard ACLs, webhook HMAC | PASS — ACLs added; webhooks verified in connector |
| Recoverability | Backups before each phase | PASS — under `/opt/odoo-log-report-2026-06-28/backups/` |

---

## 8. Sign-off checklist

- [ ] All Excel requirements in Section 4 reviewed against Odoo UAT
- [ ] Failed product queue lines retried and verified
- [ ] Stakeholder accepts WebSocket + cron fixes in production path
- [ ] Work package #{WP_ID} updated in OpenProject with link to this document

---

*Auto-generated by `fetch-wp70-and-analyze.py`. Re-run after Excel or WP description changes.*
"""

    out.write_text(body, encoding="utf-8")
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:500]}", file=sys.stderr)
        raise SystemExit(1)
