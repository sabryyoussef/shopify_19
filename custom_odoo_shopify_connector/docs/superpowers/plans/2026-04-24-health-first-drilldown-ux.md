# Shopify Connector Health-first Drilldown UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing dashboard + order queue + error logs UX to reduce clicks and increase scanability for “detect → drill down → fix → retry”, using XML view refactors only (Odoo 19 compatible).

**Architecture:** Keep all models and backend logic unchanged; improve action flow purely via view layout, search UX (searchpanel + smarter defaults), button/context wiring, and list/form scanability. Use view inheritance/patching only and avoid introducing new screens.

**Tech Stack:** Odoo 19 XML views (list/form/search/kanban), Odoo web client searchpanel and context defaults.

---

## Scope & Constraints (hard)

- Only modify existing XML views in this module.
- Scope strictly limited to:
  - `views/dashboard_view.xml` (existing dashboard only)
  - `views/order_queue_view.xml` (order queue + sync log views in same file)
  - `views/shopify_extra_models_views.xml` (error logs only; do not refactor other models in that file)
- No new Python files.
- No new models.
- No new replacement dashboards/screens.
- No backend changes unless absolutely required to support a view-only UX improvement (avoid if possible).
- Odoo 19 compliance:
  - No `attrs=`
  - No deprecated `<tree>`; use `<list>`
  - Use modern, upgrade-safe view syntax.

## File map (what we will touch)

- Modify: `views/dashboard_view.xml`
  - Shopify dashboard kanban card actions and contexts
- Modify: `views/order_queue_view.xml`
  - Order queue list/search/form for triage UX + drilldown defaults
  - (Optional) Sync log list/search defaults if needed for drilldown from queue records
- Modify: `views/shopify_extra_models_views.xml`
  - Error log list/search/form for triage UX + drilldown actions

---

### Task 1: Dashboard “one-click drilldown” actions (quickest win)

**Files:**
- Modify: `views/dashboard_view.xml`

- [ ] **Step 1: Identify existing dashboard buttons/contexts**
  - Locate the dashboard kanban template buttons:
    - primary “Sync / Import…”
    - secondary “Queue Monitor”
    - secondary “Errors”

- [ ] **Step 2: Make “Failed jobs” and “Pending jobs” actionable**
  - Convert the “Failed jobs” row value and “Pending jobs” row value into buttons (or add adjacent buttons) that open `action_shopify_order_queue` with store-scoped filters:
    - Context should include:
      - `search_default_store_id`: store id
      - `search_default_failed`: 1 (for failed drilldown) OR `search_default_pending`: 1 (for pending drilldown)
      - `search_default_last_24h`: 1 (optional) to focus recent activity
      - `default_store_id`: store id (only if the target model uses it; safe to omit)
  - Keep it upgrade-safe: only adjust template and button contexts, no new actions.

- [ ] **Step 3: Tighten secondary CTAs**
  - “Queue Monitor” should open the Order Queue already filtered to the current store and biased toward unresolved work:
    - Prefer: `search_default_pending: 1` (and optionally `search_default_failed: 1` if you add a combined filter)
  - “Errors” should open Error Logs filtered to:
    - store = current store
    - failed/unresolved only by default

- [ ] **Step 4: Verify no new views/actions were created**
  - Confirm edits only changed the existing kanban template in `views/dashboard_view.xml`.

- [ ] **Step 5: Commit**

```bash
git add views/dashboard_view.xml
git commit -m "ui: add dashboard drilldown actions for queues and errors"
```

---

### Task 2: Order Queue list view scanability + defaults (fast triage)

**Files:**
- Modify: `views/order_queue_view.xml`

- [ ] **Step 1: Improve list columns for triage**
  - In `view_shopify_order_queue_list`, reorder columns for “scan → decide → click”:
    - Put status badge early (after store/id)
    - Ensure `write_date` (Last Run) is visible if available (already in header on form; add to list if present on model)
    - Keep `error_summary` visible but later in row so status reads first.
  - Add `default_order="write_date desc, create_date desc"` on the list view to surface most recent runs.

- [ ] **Step 2: Make status more legible**
  - Keep `widget="badge"` and ensure decorations cover pending/processing/failed/done consistently (already present).
  - If you have both `state` and a retry-related field, consider showing retry count in list (only if field exists on model; do not add backend fields).

- [ ] **Step 3: Add searchpanel to reduce clicks**
  - In `view_shopify_order_queue_search`, add a `<searchpanel>` block:
    - One category for store (e.g. `store_id`)
    - One category for state (e.g. `state`)
  - Preserve existing filters (Pending/Processing/Failed/Done/Last 24h/Group by store).

- [ ] **Step 4: Default to “unresolved work”**
  - In the `action_shopify_order_queue` context, keep `search_default_pending: 1`.
  - Optional upgrade-safe improvement:
    - Add a new filter “Unresolved” with domain `state in ('pending','processing','failed')`
    - Set `search_default_unresolved: 1` instead of only pending
  - Only do this if it doesn’t conflict with your existing “pending” default; otherwise keep current default.

- [ ] **Step 5: Commit**

```bash
git add views/order_queue_view.xml
git commit -m "ui: improve order queue list/search triage and defaults"
```

---

### Task 3: Order Queue form “fix → retry” action flow (reduce clicks)

**Files:**
- Modify: `views/order_queue_view.xml`

- [ ] **Step 1: Header button hierarchy**
  - Ensure failed-state primary CTA is visually dominant:
    - “Retry Failed” stays `btn-primary` and only visible when failed.
  - De-emphasize less frequent actions:
    - “Reset to Pending”, “Open Sync Logs”, “Open Related Records” as `btn-secondary` / `btn-light` depending on your existing styling pattern.

- [ ] **Step 2: Place “what to do next” guidance next to the failure signal**
  - You already have a “Needs attention” alert visible on failed.
  - Tighten microcopy to:
    - explicitly mention “open logs” and “open related records”
    - mention “retry” as the last step.

- [ ] **Step 3: Make “Open Sync Logs” pre-filtered**
  - If `action_open_logs` currently opens logs unfiltered, adjust its context on the button (XML-only) so the target view opens filtered by:
    - store_id = current store
    - and ideally shopify order id/reference if available in `shopify.sync.log`
  - Keep this change upgrade-safe by using `context` on the button, not new backend methods.

- [ ] **Step 4: Commit**

```bash
git add views/order_queue_view.xml
git commit -m "ui: tighten order queue form actions and log drilldown"
```

---

### Task 4: Error Logs list view becomes triage-grade (scan & decide)

**Files:**
- Modify: `views/shopify_extra_models_views.xml`

- [ ] **Step 1: Improve list columns**
  - In `view_shopify_error_log_tree` (list), optimize for triage:
    - Put `state` badge early
    - Add `create_date` and/or `write_date` ordering (default_order)
    - Add a **message preview** column if the model exposes a short text field (likely `message`)
      - If `message` is long text, keep it but place late; Odoo will truncate in list.
  - Add `default_order="create_date desc"` to show newest failures first.

- [ ] **Step 2: Add searchpanel**
  - In `view_shopify_error_log_search`, add `<searchpanel>`:
    - store (`instance_id`)
    - state (`state`)
  - Keep existing filters (failed/resolved + group_by store).

- [ ] **Step 3: Make Error Logs default to failed**
  - Update `action_shopify_error_log` to set context `{ 'search_default_failed': 1 }`
  - Preserve manual access to resolved items via filter.

- [ ] **Step 4: Commit**

```bash
git add views/shopify_extra_models_views.xml
git commit -m "ui: upgrade error logs list/search for faster triage"
```

---

### Task 5: Error Log form “next steps” drilldowns (reduce clicks)

**Files:**
- Modify: `views/shopify_extra_models_views.xml`

- [ ] **Step 1: Add a small “Next steps” action area**
  - Without creating a new screen, add header buttons (or a button box) for:
    - “API Logs” → opens existing `action_shopify_api_log`
    - “Sync Logs” → opens existing `action_shopify_sync_log`
  - Both should be pre-filtered by store (`instance_id`) and, if feasible, by Shopify reference (`shopify_id`) via context search defaults.

- [ ] **Step 2: Improve the error message presentation**
  - Keep “What happened” alert, but add a second short block “What to do next” describing:
    - 1) open logs, 2) fix config/mapping, 3) retry the queue job.
  - Keep microcopy concise and Odoo-native.

- [ ] **Step 3: Commit**

```bash
git add views/shopify_extra_models_views.xml
git commit -m "ui: add drilldown actions and guidance to error log form"
```

---

### Task 6: Cross-wiring consistency (dashboard ↔ queues ↔ errors)

**Files:**
- Modify: `views/dashboard_view.xml`
- Modify: `views/order_queue_view.xml`
- Modify: `views/shopify_extra_models_views.xml`

- [ ] **Step 1: Align naming & labels**
  - Ensure button labels are consistent across the flow:
    - Dashboard: “Queue” / “Order Queue” / “Errors”
    - Queue forms: “Open Sync Logs” / “Open Related Records”
    - Error form: “API Logs” / “Sync Logs”

- [ ] **Step 2: Validate contexts use the same store key**
  - Choose one consistent search default name for store filtering:
    - For queues: `search_default_store_id`
    - For error logs: `search_default_instance_id`
  - Use those consistently from dashboard drilldowns.

- [ ] **Step 3: Commit**

```bash
git add views/dashboard_view.xml views/order_queue_view.xml views/shopify_extra_models_views.xml
git commit -m "ui: unify drilldown contexts across dashboard, queue, and errors"
```

---

## Verification (required, no server assumed)

- [ ] **Step 1: Ensure no new Python files were created**

```bash
git status --porcelain
```

Expected: Only modifications to the three XML files above (and this plan doc).

- [ ] **Step 2: Confirm no `attrs=` and no `<tree>` were introduced**

```bash
rg -n "\\battrs\\s*=" views || true
rg -n "<\\s*tree\\b" views || true
```

Expected: no matches.

- [ ] **Step 3: Basic XML sanity**
  - Open the modified XML and ensure tags are balanced and view records remain valid.

---

## Post-implementation review

- [ ] Run the code-reviewer pass (focus: upgrade-safety, scope discipline, Odoo 19 compliance).
- [ ] Run verification-before-completion checklist above again after the review fixes.

