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

v1.1: handles files that arrive via rename (many sync clients write a
temp name and rename into place, which a plain on_created watch will
silently miss), waits for a copy to actually finish before reading it
instead of a fixed pause, writes output atomically so a crash never
leaves a half-written .md behind, re-converts a file if its content
changes even when the filename doesn't, and retries a failed
conversion a few times before giving up and leaving a `.failed`
marker next to the source so failures are visible instead of only
living in a log file.

Setup:
    pip install --upgrade pymupdf4llm watchdog

Usage:
    python3 pdf_inbox_watcher.py "/path/to/your/inbox"

Leave it running in a terminal, or set it up as a background service
(see the README for macOS/Windows/Linux instructions).
"""

import hashlib
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("pdf_inbox_watcher")

# --- tunables -----------------------------------------------------------
POLL_INTERVAL = 0.5      # seconds between stability checks
# 3 consecutive matching (size, mtime) reads => a full 1.0s stability window
# (2 poll gaps). A 2-check / 0.5s window is too easy for a real sync client's
# natural inter-chunk pause to slip past unnoticed; 1.0s gives real margin
# while still resolving well inside SETTLE_TIMEOUT.
STABLE_CHECKS_REQUIRED = 3
SETTLE_TIMEOUT = 30.0    # give up waiting for the file to stop changing
MAX_ATTEMPTS = 3         # conversion attempts before giving up
BACKOFF_BASE = 1.0       # seconds; attempt N waits BACKOFF_BASE * 2**(N-1)
HASH_CHUNK = 1 << 20
# -------------------------------------------------------------------------


def _converter():
    """Lazy import so tests can monkeypatch convert_pdf() without pymupdf4llm installed."""
    import pymupdf4llm
    return pymupdf4llm


def convert_pdf(pdf_path: Path) -> str:
    """Do the actual PDF->markdown conversion. Isolated for easy mocking in tests."""
    return _converter().to_markdown(str(pdf_path))


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def hash_sidecar(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".hash")


def failed_sidecar(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".failed")


def md_path_for(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".md")


def atomic_write_text(target: Path, content: str) -> None:
    """Write to a same-directory temp file, then os.replace() — atomic on POSIX,
    so a crash mid-write can never leave a partial/corrupt target file."""
    tmp = target.with_name(target.name + f".tmp{os.getpid()}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)  # atomic rename on the same filesystem
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def wait_until_stable(
    path: Path,
    poll_interval: float = POLL_INTERVAL,
    stable_checks_required: int = STABLE_CHECKS_REQUIRED,
    timeout: float = SETTLE_TIMEOUT,
) -> bool:
    """Poll size+mtime until they stop changing across N consecutive checks,
    or give up after `timeout` seconds. Returns True if stable, False on timeout
    or if the file vanished (e.g. a truly transient temp write)."""
    deadline = time.monotonic() + timeout
    last = None
    consecutive = 0

    while time.monotonic() < deadline:
        try:
            st = path.stat()
        except FileNotFoundError:
            return False
        current = (st.st_size, st.st_mtime)

        if current == last:
            consecutive += 1
            if consecutive >= stable_checks_required:
                return True
        else:
            consecutive = 1  # this read counts as the first of a new run
        last = current
        time.sleep(poll_interval)

    return False


def write_failed_sidecar(pdf_path: Path, reason: str) -> None:
    try:
        failed_sidecar(pdf_path).write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{reason}\n",
            encoding="utf-8",
        )
    except OSError as e:
        log.error(f"Could not even write .failed sidecar for {pdf_path.name}: {e}")


def clear_failed_sidecar(pdf_path: Path) -> None:
    fp = failed_sidecar(pdf_path)
    if fp.exists():
        try:
            fp.unlink()
        except OSError:
            pass


def already_converted(pdf_path: Path, current_hash: str) -> bool:
    """True only if a .md exists AND the recorded hash of the source that
    produced it matches the current source's hash — i.e. nothing changed."""
    md = md_path_for(pdf_path)
    hp = hash_sidecar(pdf_path)
    if not (md.exists() and hp.exists()):
        return False
    try:
        stored = hp.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stored == current_hash


def convert(pdf_path: Path) -> None:
    if not pdf_path.exists():
        return  # e.g. renamed again before we got to it

    # --- Fix #2: poll-based settle instead of a fixed sleep -------------
    if not wait_until_stable(pdf_path):
        log.error(f"{pdf_path.name} never stabilized (still changing after {SETTLE_TIMEOUT}s or vanished)")
        if pdf_path.exists():
            write_failed_sidecar(pdf_path, f"File did not stabilize within {SETTLE_TIMEOUT}s (still being written?).")
        return

    try:
        current_hash = file_hash(pdf_path)
    except OSError as e:
        log.error(f"Could not hash {pdf_path.name}: {e}")
        write_failed_sidecar(pdf_path, f"Could not read file to hash: {e}")
        return

    # --- Fix #4: hash comparison instead of unconditional "md exists? skip" ---
    if already_converted(pdf_path, current_hash):
        log.info(f"Unchanged since last conversion, skipping: {pdf_path.name}")
        return

    log.info(f"Converting: {pdf_path.name}")

    # --- Fix #5: bounded retry with backoff, durable failure record -----
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            markdown = convert_pdf(pdf_path)
            break
        except Exception as e:
            last_error = e
            log.error(f"Attempt {attempt}/{MAX_ATTEMPTS} failed for {pdf_path.name}: {e}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
    else:
        write_failed_sidecar(
            pdf_path,
            f"Conversion failed after {MAX_ATTEMPTS} attempts.\n"
            f"Last error: {last_error}\n"
            f"{''.join(traceback.format_exception(type(last_error), last_error, last_error.__traceback__)) if last_error else ''}",
        )
        return

    # --- Fix #3: atomic write (temp + os.replace) ------------------------
    try:
        atomic_write_text(md_path_for(pdf_path), markdown)
        hash_sidecar(pdf_path).write_text(current_hash, encoding="utf-8")
    except OSError as e:
        log.error(f"Failed to write output for {pdf_path.name}: {e}")
        write_failed_sidecar(pdf_path, f"Conversion succeeded but writing output failed: {e}")
        return

    clear_failed_sidecar(pdf_path)
    log.info(f"Wrote: {md_path_for(pdf_path).name} ({len(markdown)} chars)")


class InboxHandler(FileSystemEventHandler):
    def _handle(self, path_str: str):
        path = Path(path_str)
        if path.suffix.lower() != ".pdf":
            return
        convert(path)

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(event.src_path)

    # --- Fix #1: rename-into-place never fires on_created ----------------
    def on_moved(self, event):
        if event.is_directory:
            return
        # dest_path is where the file landed after the rename — that's
        # what needs processing, not the (already gone) temp source name.
        self._handle(event.dest_path)


def sweep_existing(inbox: Path):
    existing = sorted(inbox.glob("*.pdf"))
    if existing:
        log.info(f"Found {len(existing)} PDF(s) already waiting, checking...")
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
