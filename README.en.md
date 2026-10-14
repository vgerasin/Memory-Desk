# Memory Desk

[Русский](README.md) · **English** · [Français](README.fr.md)

A fully local AI meeting desk for macOS. It takes audio from a PLAUD or any other recorder, transcribes it, pulls out decisions, tasks, risks and open questions, files the notes in Obsidian and answers questions across your whole archive of recordings. **Audio, transcripts and notes never leave your computer**: transcription, analysis and search all run on local models.

![The “Today” screen](docs/img/today.png)

> All images in this repository were made with the fictional data from `tests/make_fixture.py`. The app itself was written for Russian-language meetings: notes and transcripts stay in Russian, only the interface is translated (Russian, English, French, German, Spanish — machine-drafted translations that still need a native speaker’s review).

![From file to answer: queue, record, minutes, chat](docs/img/workflow.gif)

*The “drop a file, get minutes” path through the screens (slides made on fictional data, not a recording of a real processing run; the captions in the animation are in Russian).*

## Why it exists

I bought a PLAUD recorder — a great device, but I wanted to build my own: **local, on a MacBook, with no subscriptions and no cloud services**. The idea is simple: you drop a recording into a folder and get roughly the same result — transcript, summary, decisions, tasks, risks and questions — for free, with the recordings never leaving your computer. Memory Desk is an independent project and has no connection to PLAUD; it works with audio files from any recorder.

## What it gives you

- **Recording → finished note, no manual work.** Drop a file in a folder and get a time-coded transcript, a title, a summary, decisions, tasks (with owner and due date), risks and questions. A template sets the note’s structure: “Meeting with decisions”, “Stand-up”, “One-on-one”, “Interview”, “Lecture”, plus focused ones (“Decisions only”, “Tasks by person”, “Questions for the client”, “Risks”, “Five lines for the boss”); you can also build your own from a description.
- **One desk instead of a pile of notes.** A person page and a project page gather commitments, decisions, questions and dynamics; people cards can be created by hand (they live in Obsidian). A morning brief (“what’s on today, what’s overdue, what was promised to you”), a registry of tasks and decisions with drafts waiting for confirmation, stuck tasks, series of recurring meetings, and a warning when a new decision contradicts an older one.
- **Attachments.** Photos of a whiteboard, slide or sheet and text files can be attached to a recording; text in photos is recognised on your computer (macOS Vision), editable, and searchable by search and chat.
- **Control and privacy.** An activity log (what changed and when, without the content of recordings, with an integrity check), “erase a recording completely” with a list and confirmation, a PIN lock with a data-free screen. The PIN does not encrypt files — that is what FileVault is for.
- **Command line and MCP.** `./memory-desk ask | search | list --since 7d | ingest | status` and an MCP server for Claude Desktop: search with sources, records, tasks, dossiers. Access only through a token from Settings (read-only by default, writing is switched on separately); every call shows in the log.
- **Suggestions after a meeting.** Agreements found in the recording (“let’s talk on Thursday”, “send the minutes”, “remind me by Friday”) appear as cards; a Calendar event, a Reminder or an email draft is created only when you press the button.
- **Interface languages.** Russian, English, French, German, Spanish (machine-drafted translations, review needed). Notes and transcripts stay in Russian.
- **Recognition quality.** Choice of Whisper model, a hint made of the dictionary, people and project names, audio preparation; doubtful spots in the transcript are marked “check by ear”; words you corrected are offered for the dictionary; `scripts/asr_benchmark.py` measures what works best on your own recordings.
- **Bookmarks.** The B key on the player drops a coloured mark with a comment; bookmarks live in the note itself and are searchable.
- **Ask the archive** in plain language. The answer is built only from your recordings and carries sources with timestamps; below it are follow-up suggestions and a “Save to Obsidian” button. If there is no evidence, the chat says so.
- **Materials on demand.** Meeting minutes, a mind map, a project digest, a memo, a brief and an email draft are assembled from the extracted facts and work even without the model; every item in the memo, brief and email carries a timestamp into the recording.
- **Export.** Minutes and chat answers to PDF, Word, HTML and Markdown; the map to PNG. Cyrillic in PDF does not depend on system fonts.
- **Obsidian stays a readable archive.** Notes and transcripts are plain Markdown files; SQLite keeps working statuses, Qdrant keeps semantic search.
- **Fully local.** No cloud APIs, no accounts, the server listens only on `127.0.0.1`.

A detailed list of features by screen is in [docs/FEATURES.md](docs/FEATURES.md) (Russian), the system design in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How it works

```text
PLAUD / recorder ─► recordings folder ─► MLX Whisper ─► Ollama (qwen3:4b) ─► note + transcript in Obsidian
                                          transcript     decisions, tasks,         │
                                          with timecodes risks, questions          ▼
                                                                     SQLite (statuses, links)
                                                                     Qdrant (semantic search)
                                                                             │
                                                           FastAPI :8502 ◄───┘───► React interface
```

| Stage | What does it |
| --- | --- |
| Transcription | `mlx-whisper`, model `whisper-large-v3-turbo`, a filter for “hallucinations” on music and noise |
| Analysis and chat | Ollama, `qwen3:4b` (its own instance on `127.0.0.1:11435`) |
| Vector search | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` + Qdrant Local |
| Server | FastAPI + SSE (live progress without reloading the page) |
| Interface | React 18 + TypeScript + Vite, light and dark themes |
| Storage | SQLite (WAL), Obsidian notes, Qdrant Local |

## Requirements

- A Mac with **Apple Silicon** (M1 or newer), macOS 13 or later.
- **16 GB** of RAM recommended; with less, the installer warns you and long recordings may process slowly.
- About **12 GB** of free space: Whisper, Ollama and embedding models, the Python environment.
- Internet only during installation (packages and models). After that the desk works offline.

Intel Macs are not supported: MLX Whisper runs only on Apple Silicon.

## Installation

There are two ways, with the same result: the cabinet runs on your Mac and your data never leaves the computer.

| Way | For whom | How |
| --- | --- | --- |
| **Browser** | you want control over every step | download the repository and run the script (below); the cabinet opens in the browser |
| **Mac app** | you want an ordinary application | build or download `MemoryDesk.app` ([`macos/`](macos/README.md)); on first launch the app offers to install itself |

In both cases the **first-launch wizard** in the cabinet checks what is missing (Python packages, ffmpeg, Ollama), lets you choose the analysis and speech-recognition models and downloads them **on your click**: with sizes, progress and a stop button. Recording and notes folders and access to Calendar and Reminders are set up there too. Later the wizard opens from “Settings → Data → System check”. `install.command --skip-models` installs only the environment and leaves the models to the wizard; without the flag it downloads everything itself.

One command (fetches the code into `~/memory-desk` and runs the installer):

```bash
curl -fsSL https://raw.githubusercontent.com/vgerasin/Memory-Desk/main/scripts/bootstrap.sh | zsh
```

Or manually:

```bash
git clone https://github.com/vgerasin/Memory-Desk.git memory-desk
cd memory-desk
./install.command
```

If `git` is not yet installed on the Mac, macOS offers to install the Command Line Tools — agree and repeat the command.

If you downloaded the project as a ZIP, macOS quarantines the files — clear the flag before running:

```bash
xattr -dr com.apple.quarantine .
chmod +x *.command meeting-assistant meetings-process
./install.command
```

The installer (`install.command`) in turn:

1. checks the system: Apple Silicon, macOS version, free space, memory;
2. installs `python@3.12`, `ffmpeg` and `ollama` through Homebrew if missing;
3. creates the `.venv` environment and installs the packages from `requirements.txt`;
4. creates the working folders (`logs`, `runtime`, `webui/data`, `work`) and clears quarantine;
5. starts its own Ollama instance and downloads `qwen3:4b` into `ollama-models/` inside the project;
6. pre-downloads the embedding model and the Whisper model into `huggingface-cache/` so everything later works offline;
7. prints a summary and opens the desk.

Running it again is safe: finished steps are skipped and user data is not touched.

| Flag | Effect |
| --- | --- |
| `--check` | only check the state, change nothing |
| `--skip-models` | do not download models (for re-setup without network) |
| `--no-open` | do not open the desk when finished |
| `--help` | help |

Installation log: `logs/install.log`.

After installation open the gear → “Settings” and choose the recordings folder, the Obsidian folder and how long to keep audio.

> **The installer has so far been tested only against stubs** (see [“What has been verified”](#what-has-been-verified)): step order, idempotence, refusal on Intel and on low disk space are covered by `tests/test_installer.py`, but it has not yet been run on a clean Mac with real downloads. If something goes wrong, send `logs/install.log`.

## Daily use

1. Put audio files into the chosen recordings folder (or turn on auto-processing in Settings).
2. Run `start.command` — the desk opens at `http://127.0.0.1:8502`.
3. Press “Process new” (for each queued file you can choose a note template). Progress (12 stages, percentage, time left) streams in real time.
4. Open the finished recording under “Records”: assign a project, type and participants, confirm the decisions and tasks that were found, publish it to Obsidian. While files wait for processing they sit in the “New and processing” block at the top of the same screen; the note template is chosen there too.
5. Ask questions about the archive: `⌘K`.

MP3, WAV, M4A, AAC, FLAC, OGG and MP4 are supported. Duplicates are detected by SHA-256. On failure the transcript and intermediate results are kept, and processing resumes from the finished stage.

## Data and privacy

All data lives inside the project folder and in `~/Library/Application Support/MemoryDesk/`:

| Data | Path |
| --- | --- |
| Settings | `~/Library/Application Support/MemoryDesk/config.json` |
| SQLite | `webui/data/meetings.db` (or `data/` if the folder does not exist) |
| Qdrant | `webui/data/qdrant` |
| Database backups | `webui/data/backups` |
| Logs | `logs/` |
| Intermediate results | `work/<recording-name>` |
| Models | `ollama-models/`, `huggingface-cache/` |

The recordings folder and the Obsidian vault are chosen in Settings and can be anywhere.

- The server listens only on `127.0.0.1`, accepts requests only with the `127.0.0.1`/`localhost` host, and requires the `X-Memory-Desk` header on any changing request: an arbitrary website in your browser cannot control the desk.
- Voice fingerprints (the optional “By voice” feature) are **biometric data**. They are stored locally only, added by a person’s action with a consent confirmation, and deleted under “People”.
- `.gitignore` excludes audio, transcripts, databases, the search index, models, logs, backups and local settings. **Do not publish your data**: the repository contains only code, and the test data is fictional and generated by a script.

## Development

```bash
# environment for development and tests
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx pypdf

# server (port 8502)
.venv/bin/python -m memory_desk

# interface with hot reload (port 5173, proxies /api to 8502)
cd frontend && npm install && npm run dev

# build the interface into memory_desk/web
cd frontend && npm run build

# tests (the embedding model is replaced by a stub)
MEMORY_DESK_FAKE_EMBED=1 .venv/bin/python -m pytest -q tests
```

Test datasets:

```bash
python tests/make_fixture.py /tmp/demo     # a small “human” set
python tests/make_stress.py  /tmp/stress   # ~55 recordings with edge cases
```

Both commands print `export` lines with paths — run them and start `python -m memory_desk` to look at the desk on fictional data.

Repository layout:

```text
memory_desk/       server: API, queue, search, summaries, integrations
frontend/          interface sources (React + Vite)
memory_desk/web/   built interface (Node.js is not needed to run)
macos/             macOS app draft (Swift) — not built yet
meetings_process.py   recording processing pipeline (CLI)
tests/             server and installer tests, test-data generators
docs/              features, architecture, implementation status
install.command    installer; start.command — start the desk
```

## What has been verified

- **Automatically** (`pytest`, 505 tests): API and protection, filters and search, the record card, publishing, tasks and decisions, analytics, chat summaries, minutes and map, memo, brief and email with timecode checks, project digest, decision tree, calendar integration, morning brief, stuck tasks and series, note templates and their rollback, bookmarks, question hints, export (PDF, DOCX, HTML, Markdown, PNG), resilience of notes to “hostile” edits, stubbed EventKit and voice encoder, installer scenarios on stubs, the activity log, PIN lock, live dossiers, interface languages.
- **In a browser** (Chromium, fictional data): all screens in light and dark themes, a stress set of ~55 recordings with no JavaScript errors and no 500 responses; PDF, DOCX and map images inspected by eye (DOCX opened in LibreOffice); the phone page in a 390×844 window.

**Not verified on real hardware** (the development environment has no Apple Silicon): MLX Whisper transcription and Ollama analysis through the new interface, the quality of `qwen3:4b` answers and summaries, loading the real embedding model, reading Calendar and Reminders through EventKit, the ECAPA voice model and similarity threshold, the Whisper filter on real music and noise, opening DOCX in Word and Pages, installation on a clean Mac, the macOS app shell (never built), the quality of the translations. Run the first launch on one short file.

The full list of limitations is in [docs/STATUS.md](docs/STATUS.md) (Russian).

## What next

The detailed plan with acceptance criteria is in [docs/ROADMAP.md](docs/ROADMAP.md) (Russian). Nearest:

- a first-run and installation wizard (model choice and download, dependency checks);
- the **macOS app** (window, menu-bar icon, Shortcuts): a draft is in [`macos/`](macos/README.md); it has not been built and awaits a check on a Mac; signing, notarisation and auto-update are not done.

## License

[MIT](LICENSE).
