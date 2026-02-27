"""Install a reference skill from a GitHub URL (Agent Skills format: SKILL.md + references/)."""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _normalize_skill_id(name: str) -> str:
    """e.g. swiftui-expert-skill -> swiftui_expert_skill"""
    return re.sub(r"[^a-z0-9_]", "_", name.lower().strip()).strip("_") or "skill"


def _find_skill_folder(repo_root: Path, explicit_folder: Optional[str]) -> Tuple[Path, str]:
    """Return (path to folder containing SKILL.md, suggested skill_id)."""
    if explicit_folder:
        folder = repo_root / explicit_folder
        if folder.exists() and (folder / "SKILL.md").exists():
            return folder, _normalize_skill_id(explicit_folder)
        if folder.exists():
            return folder, _normalize_skill_id(explicit_folder)
    if (repo_root / "SKILL.md").exists():
        return repo_root, _normalize_skill_id(repo_root.name)
    for d in repo_root.iterdir():
        if d.is_dir() and (d / "SKILL.md").exists():
            return d, _normalize_skill_id(d.name)
    raise FileNotFoundError("No SKILL.md found in repo root or any subfolder. Specify --skill-folder.")


def install_skill_from_url(
    url: str,
    skill_folder: Optional[str] = None,
    data_dir: Optional[Path] = None,
    skill_id: Optional[str] = None,
) -> str:
    """
    Clone repo from URL, find folder with SKILL.md, copy to data_dir/plugins/skills/<skill_id>/,
    and write a stub plugin .py that registers the skill with reference_dir.
    Returns the skill_id (e.g. swiftui_expert_skill).
    """
    data_dir = data_dir or Path.home() / ".grizzyclaw"
    plugins_dir = data_dir / "plugins" / "skills"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="grizzyclaw_skill_") as tmp:
        tmp_path = Path(tmp)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--single-branch", url, str(tmp_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as e:
            stderr = e.stderr or ""
            raise RuntimeError(f"git clone failed: {stderr.strip() or e}") from e
        except FileNotFoundError:
            raise RuntimeError("git is required to install skills from URL. Install git and try again.") from None

        source_folder, suggested_id = _find_skill_folder(tmp_path, skill_folder)
        sid = _normalize_skill_id(skill_id) if skill_id else suggested_id
        if not sid:
            sid = "installed_skill"

        dest_folder = plugins_dir / sid
        if dest_folder.exists():
            shutil.rmtree(dest_folder)
        shutil.copytree(source_folder, dest_folder, dirs_exist_ok=False)

        plugin_file = plugins_dir / f"{sid}.py"

        skill_name = sid.replace("_", " ").title()
        readme = dest_folder / "README.md"
        if readme.exists():
            try:
                first_line = readme.read_text(encoding="utf-8", errors="replace").strip().split("\n")[0]
                if first_line.startswith("#"):
                    skill_name = first_line.lstrip("#").strip()
            except Exception:
                pass

        stub = f'''"""Reference skill: {skill_name} (installed from URL). Content from SKILL.md is injected into the agent prompt when this skill is enabled."""

from pathlib import Path

_REF_DIR = Path(__file__).parent / "{sid}"

SKILL_METADATA = {{
    "id": "{sid}",
    "name": "{skill_name}",
    "description": "Reference skill (Agent Skills format). Follow SKILL.md and references when relevant.",
    "icon": "📚",
    "reference_dir": str(_REF_DIR.resolve()),
}}
'''
        plugin_file.write_text(stub, encoding="utf-8")
        logger.info("Installed skill %s from %s -> %s", sid, url, dest_folder)
        return sid
