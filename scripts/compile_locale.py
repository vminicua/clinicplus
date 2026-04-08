from __future__ import annotations

from pathlib import Path

import polib


BASE_DIR = Path(__file__).resolve().parents[1]
LOCALE_DIR = BASE_DIR / "locale"


def compile_catalogs() -> None:
    for po_path in LOCALE_DIR.rglob("django.po"):
        catalog = polib.pofile(po_path)
        catalog.save_as_mofile(po_path.with_suffix(".mo"))


if __name__ == "__main__":
    compile_catalogs()
