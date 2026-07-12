# Inbox Ingestor

Drop a PDF in a folder. Get clean Markdown a few seconds later. Fully offline — no API key, no rate limits, no cloud dependency, no cost.

## Why

If you use an LLM assistant (Claude, ChatGPT, etc.) with filesystem access as part of an Obsidian "second brain" workflow, you've probably hit this: PDFs are unreliable for these tools to read directly — broken tool calls, truncated extraction, or the assistant just asking you to paste the text in manually. Cloud-based conversion (Gemini, GPT-4V, etc.) fixes quality but adds API keys, rate limits, and billing setup for what should be a simple, boring, repeatable task.

This tool sidesteps all of it: a folder watcher that converts PDFs to Markdown **locally**, using [`pymupdf4llm`](https://github.com/pymupdf/RAG). No network call, no key, no quota, no bill.

## What it does — and doesn't do

**Does:** watches a folder, converts any `.pdf` that appears into a `.md` file with the same name, right next to it. Preserves headings and tables reasonably well for text-based PDFs.

**Doesn't:** file the note anywhere, categorize it, or make any decisions about your vault structure — that's a separate step, and honestly one your LLM assistant is much better suited for than a script. This tool solves the *reading* problem, not the *organizing* problem.

**Won't work well on:** scanned/image-only PDFs (no embedded text layer) — those need OCR, which this doesn't do. Text-based PDFs (the vast majority of papers, applications, reports) work well.

## Setup

Works identically on macOS, Windows, and Linux — the watcher itself is pure Python.

```bash
pip install --upgrade pymupdf4llm watchdog
```

Then run it, pointing at whatever folder you want watched:

```bash
python3 pdf_inbox_watcher.py "/path/to/your/inbox"
```

Leave that terminal window open and it'll watch continuously. Drop a PDF in the folder, a `.md` file appears next to it within a couple seconds.

### Running it in the background permanently

If you want it to survive reboots and start automatically, pick your OS below.

<details>
<summary><b>macOS (launchd)</b></summary>

1. Edit `com.inboxingestor.watcher.plist`, replacing the two `REPLACE_WITH_FULL_PATH_TO` placeholders with your actual paths to the script and your inbox folder.
2. Move it into place and load it:
   ```bash
   mv com.inboxingestor.watcher.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.inboxingestor.watcher.plist
   ```
3. Verify it's running:
   ```bash
   launchctl list | grep inboxingestor
   ```
   You should see an actual process ID (not `-`) in the output.

**Common gotcha:** if your inbox folder is inside `Desktop`, `Documents`, or `Downloads`, macOS's privacy protections (TCC) will block the background process from reading it, even though it works fine when you run the script manually in Terminal. If you see `Operation not permitted` in the error log, go to **System Settings → Privacy & Security → Full Disk Access**, and add `/usr/bin/python3` (or wherever `which python3` points) to the list. Note: the Full Disk Access file picker sometimes doesn't show raw binaries — drag the file into the list from Finder instead if the picker won't cooperate.

To stop it: `launchctl unload ~/Library/LaunchAgents/com.inboxingestor.watcher.plist`

</details>

<details>
<summary><b>Windows (Task Scheduler)</b></summary>

1. Open **Task Scheduler** (search for it in the Start menu).
2. **Create Task** (not "Basic Task" — you want the full dialog for more control).
3. **General tab:** name it something like `PDF Inbox Watcher`. Check "Run whether user is logged on or not" if you want it fully background, or leave default for it to run only in your session.
4. **Triggers tab → New:** set to "At log on" so it starts automatically.
5. **Actions tab → New:**
   - Program/script: `python` (or the full path from `where python` in Command Prompt)
   - Add arguments: `pdf_inbox_watcher.py "C:\path\to\your\inbox"`
   - Start in: the folder where you saved `pdf_inbox_watcher.py`
6. Save. Right-click the task and choose **Run** to test it immediately.

Check Task Scheduler's history/log for the task to confirm it launched without errors.

</details>

<details>
<summary><b>Linux (systemd user service)</b></summary>

1. Edit `inbox-ingestor.service`, replacing the placeholder paths with your actual paths to the script and inbox folder.
2. Install it as a user service:
   ```bash
   mkdir -p ~/.config/systemd/user
   cp inbox-ingestor.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now inbox-ingestor.service
   ```
3. Check it's running:
   ```bash
   systemctl --user status inbox-ingestor.service
   ```
   Should show `active (running)`.

To view logs: `journalctl --user -u inbox-ingestor.service -f`

</details>

## Pairing with an LLM assistant (the actual point of this)

The watcher solves conversion. Filing/organizing the resulting notes is a separate, judgment-heavy step — that's where your LLM assistant comes in, if it has filesystem access to your vault (e.g. via an MCP filesystem server for Claude, or a similar plugin for other tools).

A simple pattern that works well:
1. Drop PDFs into your inbox folder throughout the week.
2. The watcher silently converts each to markdown as it lands.
3. Periodically, open a session with your assistant and say something like *"process the inbox"* — have it read each converted file, decide what kind of note it should become, and file it into the right place in your vault.

This tool is intentionally scoped to do the first two steps reliably and nothing else — the filing logic lives in whatever prompt/procedure doc you use with your assistant, since that's where judgment calls belong, not in a script.

## License

MIT — do whatever you want with it.
