"""Remove everything a project folder can regenerate.

    python cleanup.py            caches, debug dumps, downloads, build leftovers
    python cleanup.py --deep     also .venv and the Chrome profile (both rebuild themselves)
    python cleanup.py --dry-run  show what would go, change nothing

Never touches your code, your config, or .env.
"""
import shutil
import sys
from pathlib import Path

FOLDER = Path(__file__).resolve().parent

JUNK_DIRS = ["__pycache__", ".pytest_cache", ".mypy_cache", "debug",
             "downloads", "downloads-fidelity", ".ipynb_checkpoints"]
DEEP_DIRS = [".venv", "chrome-profile", ".chrome-profile", ".chrome-profile-fidelity"]
JUNK_FILE_PATTERNS = ["*.pyc", "*.pyo", "*.backup", "*.bak", "*.tmp", "*.log",
                      "nohup.out", ".DS_Store"]

# Files the newer project needs; anything else is listed for you to judge.
KEEP = {"fidelity.py", "chrome_session.py", "positions.py", "config.json",
        "test_positions.py", "requirements.txt", "README.md", ".gitignore",
        "_bootstrap.py", "run.bat",
        # older project
        "broker_positions.py", "config.fidelity.json", "test_parser.py", "ml.py",
        "config.json", ".env", ".env.example", "fidelity_csv.py"}


def size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def human(count: int) -> str:
    size = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.1f} {unit}"
        size /= 1024
    return f"{size:,.1f} GB"


def main() -> int:
    deep = "--deep" in sys.argv
    dry_run = "--dry-run" in sys.argv

    targets = []
    for name in JUNK_DIRS + (DEEP_DIRS if deep else []):
        for path in FOLDER.rglob(name):
            if path.is_dir() and not any(parent in targets for parent in path.parents):
                targets.append(path)
    for pattern in JUNK_FILE_PATTERNS:
        targets += [p for p in FOLDER.rglob(pattern) if p.is_file()]

    if not targets:
        print("Nothing to clean.")
    else:
        freed = 0
        for path in sorted(set(targets)):
            if not path.exists():
                continue
            amount = size_of(path)
            freed += amount
            print(f"{'would remove' if dry_run else 'removed'}  "
                  f"{path.relative_to(FOLDER)}  ({human(amount)})")
            if not dry_run:
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink(
                    missing_ok=True)
        print(f"\n{'Would free' if dry_run else 'Freed'}: {human(freed)}")

    extras = sorted(p.name for p in FOLDER.iterdir()
                    if p.is_file() and p.name not in KEEP and p.name != Path(__file__).name)
    if extras:
        print("\nFiles left over that the project does not need. Delete them yourself if "
              "you agree:\n  " + "\n  ".join(extras))

    if (FOLDER / ".env").exists():
        print("\n.env is still here (kept on purpose - it holds your login).\n"
              "Never include it when you zip or share this folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
