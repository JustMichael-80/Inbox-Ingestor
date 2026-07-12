#!/usr/bin/env python3
"""
Inbox Ingestor

Watches a folder for new PDFs and converts each to clean Markdown,
fully offline — no API key, no rate limits, no cloud dependency.

Built for people using Obsidian (or any plain-text note system) as a
"second brain" with an LLM assistant (Claude, ChatGPT, etc.) that has
filesystem access. PDFs are notoriously unreliable for LLM tools to
read directly; this solves that by pre-converting them to markdown
the instant they land in your inbox folder.

Setup:
    pip install --upgrade pymupdf4llm watchdog

Usage:
    python3 pdf_inbox_watcher.py "/path/to/your/inbox"

Leave it running in a terminal, or set it up as a background service
(see the README for macOS/Windows/Linux instructions).
"""

import sys
import time
import logging
from pathlib import Path

import pymupdf4llm
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pdf_inbox_watcher")

SETTLE_SECONDS = 2  # brief pause in case the file is still being copied/synced


def convert(pdf_path: Path):
    md_path = pdf_path.with_suffix(".md")
    if md_path.exists():
        log.info(f"Already converted, skipping: {pdf_path.name}")
        return
    try:
        log.info(f"Converting: {pdf_path.name}")
        markdown = pymupdf4llm.to_markdown(str(pdf_path))
        md_path.write_text(markdown, encoding="utf-8")
        log.info(f"Wrote: {md_path.name} ({len(markdown)} chars)")
    except Exception as e:
        log.error(f"Failed to convert {pdf_path.name}: {e}")


class InboxHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".pdf":
            return
        time.sleep(SETTLE_SECONDS)
        if path.exists():
            convert(path)


def sweep_existing(inbox: Path):
    existing = sorted(inbox.glob("*.pdf"))
    if existing:
        log.info(f"Found {len(existing)} PDF(s) already waiting, converting...")
    for pdf in existing:
        convert(pdf)


def main():
    if len(sys.argv) != 2:
        print('Usage: python3 pdf_inbox_watcher.py "/path/to/your/inbox"')
        sys.exit(1)

    inbox = Path(sys.argv[1]).expanduser().resolve()
    if not inbox.exists():
        print(f"ERROR: folder not found: {inbox}")
        sys.exit(1)

    sweep_existing(inbox)

    handler = InboxHandler()
    observer = Observer()
    observer.schedule(handler, str(inbox), recursive=False)
    observer.start()
    log.info(f"Watching {inbox} for new PDFs. Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
