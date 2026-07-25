"""Apply-patch helper tests (filesystem only)."""

from pathlib import Path

from diagnostic_engine.agent.apply_patch import apply_patches
from diagnostic_engine.config import Settings


def test_apply_patches_writes_and_backs_up(tmp_path: Path):
    sandbox = tmp_path / "app"
    sandbox.mkdir()
    target = sandbox / "main.py"
    target.write_text("OLD\n", encoding="utf-8")
    backups = tmp_path / "patches"

    settings = Settings(
        sandbox_root=sandbox,
        patch_backup_root=backups,
        target_app_root=sandbox,
        database_url="postgresql+psycopg://diagnostic:diagnostic@localhost:5433/diagnostic",
    )

    # Skip DB mark_applied / reingest by using empty session and catching — apply_patches
    # will try mark_latest_patch_applied; soft path: use a fake session and monkeypatch
    result = apply_patches(
        "00000000-0000-0000-0000-000000000000",
        {"main.py": "NEW\n", "note": "ignore me"},
        settings=settings,
        reingest=False,
    )
    assert (sandbox / "main.py").read_text(encoding="utf-8") == "NEW\n"
    assert "main.py" in "".join(result["written"]) or any("main.py" in w for w in result["written"])
    backup = backups / "00000000-0000-0000-0000-000000000000" / "main.py"
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "OLD\n"
