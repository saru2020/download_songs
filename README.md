# Tamil hits MP3 downloader

Downloads curated Tamil hit playlists from YouTube Music as tagged MP3 files, one folder per category (ARR hits, Yuvan hits, 90's tamil hits and so on), and keeps the library under a size limit, 10 GB by default.

- [playlists.md](playlists.md): the playlists, grouped into folders. It is also the downloader's config.
- [download.py](download.py): the downloader. Writes to `library/`.
- [AGENTS.md](AGENTS.md): handover notes for coding agents (status, how to verify changes, design decisions, known traps).

## Requirements

- Python 3.10 or newer
- ffmpeg: `brew install ffmpeg`
- Node.js 22 or newer, or Deno, on `PATH`. yt-dlp uses it to run YouTube's player JavaScript.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Usage

```sh
.venv/bin/python download.py --dry-run       # plan only: songs per playlist, duplicates, final size
caffeinate -i .venv/bin/python download.py   # download; caffeinate (macOS) keeps the Mac awake

# best quality: 320 kbps MP3s, library capped at 10 GB
caffeinate -i .venv/bin/python download.py --bitrate 320 --max-gb 10
```

All 868 songs need about 9.9 GB at 320 kbps and about 6 GB at the default 192 kbps (dry runs of 2026-11-13). YouTube's own audio is roughly 130 to 160 kbps Opus, so 320 kbps adds no detail; it only keeps the conversion to MP3 from losing any more.

Options:

- `--doc PATH`: playlist doc to read (default `playlists.md`)
- `--out PATH`: library folder (default `library`)
- `--max-gb N`: size limit in GB (default 10). Decimal GB as in Finder; hard-linked files count once.
- `--bitrate KBPS`: MP3 bitrate, 128, 192 (default), 256 or 320 (best). Only affects songs downloaded from then on, so to redo the whole library at another bitrate, move `library/` aside first.
- `--dry-run`: print the plan without downloading or creating anything.

Runs are resumable. Stop with Ctrl+C whenever you like and run the same command again: songs already in the library are skipped and failed songs are retried. The downloader never deletes files.

A full run is slow on purpose: it pauses 5 to 10 seconds between songs to avoid YouTube rate limits.

## Choosing playlists

[playlists.md](playlists.md) is read like this:

- Every `## heading` becomes a folder in the library, named exactly like the heading.
- Every line under it with a playlist link (anything containing `list=<playlist id>`) is downloaded into that folder. A folder can have several playlists; their songs are merged without duplicates.
- A line starting with a single `# ` ends the downloadable part, so the links under "More Tamil playlists (not downloaded)" are ignored. Move a link under a `##` heading to download it.
- `###` headings are only labels and do not create folders.

To add a category, add a `## heading` with one or more links and run the downloader again. Songs already downloaded for another folder are hard-linked instead of downloaded again. To drop a category, remove it from the doc and delete its folder yourself.

## Library layout

```
library/
├── ARR hits/
├── Deva hits/
│   └── Thanjavooru Mannu Eduthu - Porkkaalam.mp3
├── ...
├── .index.json    YouTube video id to file paths, used to resume and to spot duplicates
└── .tmp/          partial downloads
```

- File names are `<song> - <album>.mp3`, where the album is usually the movie. The album is left out for music videos without one and for singles whose title already contains it. Names come from YouTube Music rather than the raw video titles, are cut to 70 characters and are safe for FAT and exFAT drives.
- Each MP3 has title, artist, album, year, the YouTube link (as comment) and a square cover image.
- A song that appears in several folders is stored once and hard-linked into each folder, so it takes no extra space. Copying the library to a phone or USB stick turns the links into real copies, so the copy is bigger than the library on the Mac.

## How it works

1. Reads playlists.md into folders and playlist IDs.
2. Fetches each playlist from YouTube Music with ytmusicapi, keeps the songs marked available and takes their title, artists and album from YouTube Music.
3. Interleaves the folders (song 1 of every folder, then song 2, and so on), so that if the size limit is reached every folder still has its top songs.
4. For each song: skips it if the folder already has it, hard-links it if another folder has it, otherwise estimates its size and stops if it would take the library past the limit. If it fits, yt-dlp downloads the best audio, converts it to MP3, adds the tags and cover, and the song is recorded in `library/.index.json`. A failed download is retried once on a new YouTube session.
5. Stops early after 10 failed songs in a row, which usually means YouTube is rate-limiting.

## Troubleshooting

- `HTTP Error 403: Forbidden` on a song: the song is retried straight away on a new YouTube session (the "retrying" line), and again on the next run if that fails too.
- The run stops with "Too many failures in a row": YouTube is rate-limiting. Wait a while, then run again.
- Most songs fail after a YouTube change: update the libraries with `.venv/bin/pip install -U "yt-dlp[default]" ytmusicapi`.
- Warnings about a missing JavaScript runtime: install Node.js 22+ or Deno (`brew install deno`) and make sure it is on `PATH`.
- `ffmpeg not found`: `brew install ffmpeg`.
- "could not load playlist": YouTube Music removed or replaced that playlist. Find a replacement as described in [AGENTS.md](AGENTS.md#finding-more-playlists).
- With `--out` on an exFAT or FAT drive the run fails at the first duplicate, because those file systems have no hard links. Download to the Mac and copy the library over instead.

## Note

Meant for personal use. Downloading from YouTube is against its Terms of Service.
