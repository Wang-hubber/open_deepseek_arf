"""Migrate memory/*.md (app-layer format) → memory/memory.json (framework format).

Usage: python scripts/migrate_memory_md_to_json.py [--workspace ./memory]
"""
import argparse
import asyncio
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Add project root so arf imports work
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arf.memory.file_store import FileMemoryStore
from arf.core.protocols import MemoryEntry


ENTRY_RE = re.compile(
    r'^## (\w+) \((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)\n(.*?)(?=\n## \w+ \(|$)',
    re.DOTALL | re.MULTILINE,
)


def parse_md_file(path: Path) -> list[MemoryEntry]:
    """Parse category.md and return MemoryEntry list."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    entries = []
    for match in ENTRY_RE.finditer(text):
        category = match.group(1).lower()
        ts_str = match.group(2)
        content = match.group(3).strip()
        if not content:
            continue
        try:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            timestamp = dt.timestamp()
        except ValueError:
            timestamp = time.time()
        entries.append(MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            category=category,
            timestamp=timestamp,
            source_turn=0,
        ))
    return entries


async def migrate(workspace: str) -> None:
    ws = Path(workspace)
    store = FileMemoryStore(ws)

    # Load existing entries to avoid overwriting
    existing = await store._load_all()
    if existing:
        print(f"[migrate] {len(existing)} entries already in memory.json — merging")
    existing_contents = {e.content for e in existing}

    categories = ["fact", "preference", "decision"]
    new_count = 0
    for cat in categories:
        md_path = ws / f"{cat}.md"
        if not md_path.exists():
            continue
        entries = parse_md_file(md_path)
        for entry in entries:
            if entry.content in existing_contents:
                continue  # skip duplicates
            existing_contents.add(entry.content)
            await store.save(entry)
            new_count += 1
        # Archive the old file
        bak_path = ws / f"{cat}.md.bak"
        md_path.rename(bak_path)
        print(f"[migrate] {md_path.name}: {len(entries)} entries → archived to {bak_path.name}")

    total = len(existing_contents)
    print(f"[migrate] Done. {new_count} new entries merged, {total} total in memory.json")


def main():
    parser = argparse.ArgumentParser(description="Migrate .md memories to memory.json")
    parser.add_argument("--workspace", default="./memory", help="Memory workspace directory")
    args = parser.parse_args()
    asyncio.run(migrate(args.workspace))


if __name__ == "__main__":
    main()
