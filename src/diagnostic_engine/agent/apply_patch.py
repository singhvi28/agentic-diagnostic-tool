"""Apply proposed unified diffs under the sandbox with review, backup, and rollback."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from diagnostic_engine.analysis.code_reader import resolve_under_root
from diagnostic_engine.config import Settings, get_settings
from diagnostic_engine.db.repository import mark_latest_patch_applied
from diagnostic_engine.memory.ingest import ingest


def _looks_like_unified_diff(text: str) -> bool:
    lines = text.strip().splitlines()
    if not lines:
        return False
    has_minus = any(line.startswith("--- ") for line in lines)
    has_plus = any(line.startswith("+++ ") for line in lines)
    return has_minus and has_plus


def _patch_strip_level(diff_text: str) -> int:
    """Guess -pN from --- path (a/foo → 1, foo → 0)."""
    for line in diff_text.splitlines():
        if not line.startswith("--- "):
            continue
        path = line[4:].split("\t", 1)[0].strip()
        if path in {"/dev/null", "dev/null"}:
            continue
        if path.startswith("a/") or path.startswith("b/"):
            return 1
        return 0
    return 0


def _confirm_apply(
    rel_path: str,
    content: str,
    *,
    require_confirmation: bool,
    auto_yes: bool,
) -> bool:
    print(f"\n=== Proposed patch: {rel_path} ===\n{content}\n", flush=True)
    if auto_yes or not require_confirmation:
        return True
    try:
        answer = input(f"Apply {rel_path}? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _run_patch(
    *,
    sandbox: Path,
    diff_text: str,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    strip = _patch_strip_level(diff_text)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".diff",
        encoding="utf-8",
        delete=False,
    ) as tmp:
        tmp.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")
        diff_path = Path(tmp.name)

    cmd = [
        "patch",
        "--unified",
        "--forward",
        "--batch",
        f"-p{strip}",
        "-i",
        str(diff_path),
        "-d",
        str(sandbox),
    ]
    if dry_run:
        cmd.insert(1, "--dry-run")

    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        diff_path.unlink(missing_ok=True)


def apply_patches(
    session_id: str,
    proposed_patch: dict[str, str],
    *,
    settings: Settings | None = None,
    require_confirmation: bool = True,
    auto_yes: bool = False,
    dry_run: bool = False,
    reingest: bool = True,
) -> dict[str, Any]:
    """Apply unified diffs under sandbox_root with optional review and rollback.

    `proposed_patch` maps relative paths -> unified diff text.
    Keys named `note` are ignored. Full-file payloads (no ---/+++) are rejected.
    """
    settings = settings or get_settings()
    sandbox = settings.sandbox_root.resolve()

    if shutil.which("patch") is None:
        return {
            "applied": [],
            "backups": {},
            "skipped": [],
            "errors": [
                "GNU patch not found on PATH; install patch to use --apply",
            ],
            "backup_root": None,
            "patch_id": None,
        }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_root = (settings.patch_backup_root / str(session_id) / ts).resolve()
    backup_root.mkdir(parents=True, exist_ok=True)

    applied: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    backups: dict[str, str] = {}

    for rel_path, content in proposed_patch.items():
        if rel_path == "note" or not isinstance(content, str):
            skipped.append(rel_path)
            continue

        if not _looks_like_unified_diff(content):
            errors.append(
                f"{rel_path}: rejected — expected unified diff with --- and +++ "
                "(full-file payloads are not applied)"
            )
            continue

        try:
            resolve_under_root(rel_path, sandbox)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rel_path}: invalid path — {exc}")
            continue

        if not _confirm_apply(
            rel_path,
            content,
            require_confirmation=require_confirmation,
            auto_yes=auto_yes,
        ):
            skipped.append(rel_path)
            errors.append(f"{rel_path}: not applied (confirmation declined)")
            continue

        target = resolve_under_root(rel_path, sandbox)
        backup_path: Path | None = None
        if target.exists():
            backup_path = backup_root / Path(rel_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
            backups[rel_path] = str(backup_path)

        if dry_run:
            proc = _run_patch(sandbox=sandbox, diff_text=content, dry_run=True)
            if proc.returncode != 0:
                errors.append(
                    f"{rel_path}: dry-run failed — "
                    f"{(proc.stderr or proc.stdout or '').strip()}"
                )
            else:
                applied.append(rel_path)
            continue

        proc = _run_patch(sandbox=sandbox, diff_text=content, dry_run=False)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            if backup_path is not None and backup_path.exists():
                shutil.copy2(backup_path, target)
            errors.append(f"{rel_path}: patch failed — {detail}; rolled back")
            continue

        applied.append(rel_path)

    patch_id = None
    if applied and not dry_run:
        try:
            patch_id = mark_latest_patch_applied(session_id)
        except Exception:
            patch_id = None
        if reingest:
            try:
                ingest(settings.target_app_root)
            except Exception as exc:  # noqa: BLE001
                return {
                    "applied": applied,
                    "backups": backups,
                    "skipped": skipped,
                    "errors": errors + [f"reingest_error: {exc}"],
                    "backup_root": str(backup_root),
                    "patch_id": str(patch_id) if patch_id else None,
                }

    return {
        "applied": applied,
        "backups": backups,
        "skipped": skipped,
        "errors": errors,
        "backup_root": str(backup_root),
        "patch_id": str(patch_id) if patch_id else None,
    }
