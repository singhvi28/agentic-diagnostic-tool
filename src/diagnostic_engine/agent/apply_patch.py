"""Apply proposed patches under the sandbox with backups."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from diagnostic_engine.analysis.code_reader import resolve_under_root
from diagnostic_engine.config import Settings, get_settings
from diagnostic_engine.db.repository import mark_latest_patch_applied
from diagnostic_engine.memory.ingest import ingest


def apply_patches(
    session_id: str,
    proposed_patch: dict[str, str],
    *,
    settings: Settings | None = None,
    reingest: bool = True,
) -> dict[str, Any]:
    """Write patch file contents into sandbox_root, backing up originals.

    `proposed_patch` maps relative paths -> full new file content.
    Keys named `note` are ignored.
    """
    settings = settings or get_settings()
    sandbox = settings.sandbox_root.resolve()
    backup_root = (settings.patch_backup_root / str(session_id)).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[str] = []

    for rel_path, content in proposed_patch.items():
        if rel_path == "note" or not isinstance(content, str):
            skipped.append(rel_path)
            continue
        # Reject unified diffs for now — require full file content
        if content.startswith("--- ") or content.startswith("diff --git"):
            skipped.append(rel_path)
            continue

        target = resolve_under_root(rel_path, sandbox)
        if target.exists():
            backup_path = backup_root / Path(rel_path).name
            # preserve relative structure
            backup_path = backup_root / Path(rel_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

        target.write_text(content, encoding="utf-8")
        written.append(str(target))

    patch_id = None
    if written:
        try:
            patch_id = mark_latest_patch_applied(session_id)
        except Exception:
            patch_id = None
        if reingest:
            try:
                ingest(settings.target_app_root)
            except Exception as exc:  # noqa: BLE001
                return {
                    "written": written,
                    "skipped": skipped,
                    "patch_id": str(patch_id) if patch_id else None,
                    "reingest_error": str(exc),
                }

    return {
        "written": written,
        "skipped": skipped,
        "backup_root": str(backup_root),
        "patch_id": str(patch_id) if patch_id else None,
    }
