"""Apply-patch helper tests (filesystem only)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from diagnostic_engine.agent.apply_patch import apply_patches
from diagnostic_engine.config import Settings


def _settings(tmp_path: Path) -> Settings:
    sandbox = tmp_path / "app"
    sandbox.mkdir()
    return Settings(
        sandbox_root=sandbox,
        patch_backup_root=tmp_path / "patches",
        target_app_root=sandbox,
        database_url="postgresql+psycopg://diagnostic:diagnostic@localhost:5433/diagnostic",
    )


def test_apply_unified_diff_backs_up_and_writes(tmp_path: Path):
    settings = _settings(tmp_path)
    target = settings.sandbox_root / "main.py"
    target.write_text("OLD\n", encoding="utf-8")

    diff = (
        "--- main.py\n"
        "+++ main.py\n"
        "@@ -1 +1 @@\n"
        "-OLD\n"
        "+NEW\n"
    )
    result = apply_patches(
        "00000000-0000-0000-0000-000000000000",
        {"main.py": diff, "note": "ignore me"},
        settings=settings,
        require_confirmation=False,
        auto_yes=True,
        reingest=False,
    )
    assert result["errors"] == []
    assert "main.py" in result["applied"]
    assert target.read_text(encoding="utf-8") == "NEW\n"
    assert result["backups"]["main.py"]
    backup = Path(result["backups"]["main.py"])
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "OLD\n"
    assert "note" in result["skipped"]


def test_apply_rejects_full_file_payload(tmp_path: Path):
    settings = _settings(tmp_path)
    (settings.sandbox_root / "main.py").write_text("OLD\n", encoding="utf-8")

    result = apply_patches(
        "sess",
        {"main.py": "NEW\n"},
        settings=settings,
        require_confirmation=False,
        auto_yes=True,
        reingest=False,
    )
    assert result["applied"] == []
    assert any("unified diff" in e for e in result["errors"])
    assert (settings.sandbox_root / "main.py").read_text(encoding="utf-8") == "OLD\n"


def test_apply_rolls_back_on_patch_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = _settings(tmp_path)
    target = settings.sandbox_root / "main.py"
    target.write_text("OLD\n", encoding="utf-8")

    diff = (
        "--- main.py\n"
        "+++ main.py\n"
        "@@ -1 +1 @@\n"
        "-OLD\n"
        "+NEW\n"
    )

    def fake_run(cmd, **kwargs):  # noqa: ANN001
        return MagicMock(returncode=1, stdout="", stderr="patch: failed")

    monkeypatch.setattr(
        "diagnostic_engine.agent.apply_patch.subprocess.run",
        fake_run,
    )

    result = apply_patches(
        "sess",
        {"main.py": diff},
        settings=settings,
        require_confirmation=False,
        auto_yes=True,
        reingest=False,
    )
    assert result["applied"] == []
    assert any("rolled back" in e for e in result["errors"])
    assert target.read_text(encoding="utf-8") == "OLD\n"
