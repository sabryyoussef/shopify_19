#!/usr/bin/env python3
"""Queue current git HEAD for OpenProject sync."""
from __future__ import annotations
import json, subprocess
from pathlib import Path

PROFILE = {
  "id": "izone",
  "remote_repo": "/opt/odoo/custom-addons/shopify_19",
  "op_project_id": 12,
  "op_parent_wp_id": 170,
  "op_parent_subject": "izone_azone_parent",
  "subject_prefix": "[iZone]"
}
REPO = PROFILE.get("remote_repo", "/opt/localaddons/edafa_legacy_project")

def git(*args):
    return subprocess.check_output(["git", "-C", REPO, *args], text=True).strip()

def main():
    repo = Path(REPO)
    sha = git("rev-parse", "HEAD")
    queue_dir = repo / ".op-sync" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    out_file = queue_dir / f"{sha}.json"
    if out_file.exists():
        return 0
    files = subprocess.check_output(
        ["git", "-C", REPO, "diff-tree", "--no-commit-id", "--name-only", "-r", sha], text=True
    ).strip().splitlines()
    remote = ""
    try:
        remote = git("remote", "get-url", "origin")
    except subprocess.CalledProcessError:
        pass
    payload = {
        "server": PROFILE["id"],
        "project_id": PROFILE["op_project_id"],
        "parent_wp_id": PROFILE["op_parent_wp_id"],
        "parent_subject": PROFILE["op_parent_subject"],
        "subject_prefix": PROFILE["subject_prefix"],
        "commit": {
            "sha": sha,
            "subject": git("log", "-1", "--pretty=%s", sha),
            "author": git("log", "-1", "--pretty=format:%an <%ae>", sha),
            "branch": git("rev-parse", "--abbrev-ref HEAD"),
            "files": "\n".join(files),
            "remote": remote,
        },
    }
    out_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[op-sync] queued commit {sha[:12]}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
