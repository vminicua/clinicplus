from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import polib


BASE_DIR = Path(__file__).resolve().parents[1]
SCAN_DIRS = [
    BASE_DIR / "accounts",
    BASE_DIR / "clinic",
    BASE_DIR / "config",
    BASE_DIR / "templates",
]
TARGET_FILE = BASE_DIR / "locale" / "en" / "LC_MESSAGES" / "django.po"
SKIP_DIRS = {"__pycache__", "migrations", "venv", "node_modules", "staticfiles", "media"}
TEMPLATE_UI_RE = re.compile(
    r"""\{%\s*ui\s+(?P<pt>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')(?:\s+(?P<en>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))?\s*%\}"""
)


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def add_entry(
    entries: dict[str, str],
    occurrences: defaultdict[str, list[tuple[str, str]]],
    msgid: str,
    msgstr: str,
    path: Path,
    location: str,
) -> None:
    existing = entries.get(msgid)
    if existing and existing != msgstr:
        return
    entries[msgid] = msgstr
    occurrences[msgid].append((path.as_posix(), location))


class PythonTranslationVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, entries: dict[str, str], occurrences: defaultdict[str, list[tuple[str, str]]]):
        self.path = path
        self.entries = entries
        self.occurrences = occurrences

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node.func)
        args = list(node.args)

        if name in {"translate_pair", "tr"} and len(args) >= 2:
            msgid = literal_string(args[0])
            msgstr = literal_string(args[1])
            if msgid and msgstr:
                add_entry(
                    self.entries,
                    self.occurrences,
                    msgid,
                    msgstr,
                    self.path,
                    f"line {node.lineno}",
                )

        if name == "ui_text" and len(args) >= 3:
            msgid = literal_string(args[1])
            msgstr = literal_string(args[2])
            if msgid and msgstr:
                add_entry(
                    self.entries,
                    self.occurrences,
                    msgid,
                    msgstr,
                    self.path,
                    f"line {node.lineno}",
                )

        self.generic_visit(node)


def scan_python(path: Path, entries: dict[str, str], occurrences: defaultdict[str, list[tuple[str, str]]]) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    PythonTranslationVisitor(path, entries, occurrences).visit(tree)


def scan_template(path: Path, entries: dict[str, str], occurrences: defaultdict[str, list[tuple[str, str]]]) -> None:
    content = path.read_text(encoding="utf-8")
    for index, match in enumerate(TEMPLATE_UI_RE.finditer(content), start=1):
        msgid = ast.literal_eval(match.group("pt"))
        msgstr = ast.literal_eval(match.group("en")) if match.group("en") else msgid
        add_entry(entries, occurrences, msgid, msgstr, path, f"match {index}")


def collect_entries() -> tuple[dict[str, str], defaultdict[str, list[tuple[str, str]]]]:
    entries: dict[str, str] = {}
    occurrences: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)

    for scan_dir in SCAN_DIRS:
        for path in scan_dir.rglob("*"):
            if not path.is_file() or should_skip(path):
                continue
            if path.suffix == ".py":
                scan_python(path, entries, occurrences)
            elif path.suffix == ".html":
                scan_template(path, entries, occurrences)

    return entries, occurrences


def load_catalog(path: Path) -> polib.POFile:
    if path.exists():
        return polib.pofile(path)

    catalog = polib.POFile()
    catalog.metadata = {
        "Project-Id-Version": "clinicplus",
        "Report-Msgid-Bugs-To": "",
        "POT-Creation-Date": "",
        "PO-Revision-Date": "",
        "Last-Translator": "Codex",
        "Language-Team": "English",
        "Language": "en",
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Transfer-Encoding": "8bit",
    }
    return catalog


def sync_catalog() -> None:
    entries, occurrences = collect_entries()
    catalog = load_catalog(TARGET_FILE)
    TARGET_FILE.parent.mkdir(parents=True, exist_ok=True)

    for msgid, msgstr in sorted(entries.items()):
        entry = catalog.find(msgid)
        if entry is None:
            entry = polib.POEntry(msgid=msgid, msgstr=msgstr)
            catalog.append(entry)
        else:
            entry.msgstr = msgstr
        entry.occurrences = occurrences[msgid]

    catalog.sort(key=lambda item: item.msgid.lower())
    catalog.save(TARGET_FILE)
    catalog.save_as_mofile(TARGET_FILE.with_suffix(".mo"))


if __name__ == "__main__":
    sync_catalog()
