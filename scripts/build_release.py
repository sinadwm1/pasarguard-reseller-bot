"""Build the source release without local secrets or runtime data."""
from __future__ import annotations

import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "PasarGuard-Reseller-Bot-v1.6.0.zip"
TOP_LEVEL = (
    ".env.example", ".gitignore", "Dockerfile", "INSTALL_FA.md", "Makefile",
    "OPERATIONS_FA.md", "README.md", "SECURITY_FA.md", "SERVER_TEST_FA.md",
    "UPGRADE_V16_FA.md", "alembic.ini", "backup.sh", "docker-compose.yml",
    "pyproject.toml", "setup.sh",
)


def build() -> None:
    paths = [ROOT / name for name in TOP_LEVEL]
    for directory in ("alembic", "pasarbot", "scripts", "tests"):
        paths.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and path.suffix in {".py", ".mako"})
    with ZipFile(ARCHIVE, "w", ZIP_DEFLATED) as package:
        for path in sorted(paths):
            package.write(path, path.relative_to(ROOT).as_posix())
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    print(f"{ARCHIVE.name}: {ARCHIVE.stat().st_size} bytes; sha256={digest}")


if __name__ == "__main__":
    build()
