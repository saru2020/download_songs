# Agent notes

Read [README.md](README.md) first for setup, usage and the playlist doc format. This file adds what an agent needs to carry on: current status, how to verify changes safely, why things are built the way they are, and the traps already found.

## Status (2026-11-13)

- [playlists.md](playlists.md): 14 folders backed by 21 YouTube Music playlists (20 curated ones plus the top songs list from Deva's artist page, since he has no curated playlist), and 41 more Tamil playlists listed but not downloaded. Every link was checked to load; song counts are the playable songs on that date.
- [download.py](download.py): tested end to end (download, hard links, size stop, resume, tags, cover).
- Last dry run: 1063 queued tracks, which is 868 unique songs plus 195 duplicates, about 6.0 of the 10 GB limit.
- The user started the first full download on 2026-11-13 with `caffeinate -i .venv/bin/python download.py`. It may still be running. Check before touching `library/`:

```sh
pgrep -fl download.py    # a run in progress?
du -sh library           # disk used, hard links counted once
.venv/bin/python -c "import json; print(len(json.load(open('library/.index.json'))), 'songs')"
```

Never start a second run while one is going: both would download the same songs and overwrite each other's `.index.json`.

## Environment (this Mac, 2026-11-13)

- `python3` is 3.14 (uv-managed). The project venv `.venv` has yt-dlp 2026.10.26, yt-dlp-ejs 0.8.0 and ytmusicapi 1.12.3.
- ffmpeg 8.1.1 from Homebrew.
- Node 22.15.0 from nvm, no Deno. nvm only puts `node` on `PATH` in interactive shells; if yt-dlp warns about a missing JavaScript runtime, run `source ~/.nvm/nvm.sh` first or `brew install deno`.
- The machine's IP is in India, which decides which songs are playable.
- The home folder (`~`) is itself a git repo; this project is a separate repo nested inside it.

## Files

- `playlists.md`: playlist list and downloader config. Keep the line format `- [<title>](https://music.youtube.com/playlist?list=<id>) - <n> songs`; download.py only needs `list=<id>`, the count check below also reads `<n> songs`.
- `download.py`: the whole downloader in one file. There is no test suite; see "Verifying a change".
- `requirements.txt`: `yt-dlp[default]` (pulls in the yt-dlp-ejs JavaScript solver and mutagen) and `ytmusicapi`.
- `library/`: output, git-ignored. `.index.json` maps a YouTube video id to file paths relative to `library/`.
- `.venv/`: local virtualenv, git-ignored.

## Verifying a change

Do not run the full download to test anything; it takes hours and about 6 GB. Instead:

1. `.venv/bin/python -m py_compile download.py`
2. `.venv/bin/python download.py --dry-run`: every playlist prints a song count and none says "could not load". Needs network, downloads and creates nothing.
3. Small real run in /tmp, with two folders sharing one playlist and a 20 MB limit:

   ```sh
   printf '%s\n' '## A' '- https://music.youtube.com/playlist?list=RDCLAK5uy_k6fnerKTI-91d9PGK-hHzTxjkIahwl7ZA' \
     '## B' '- https://music.youtube.com/playlist?list=RDCLAK5uy_k6fnerKTI-91d9PGK-hHzTxjkIahwl7ZA' > /tmp/songs-test.md
   .venv/bin/python download.py --doc /tmp/songs-test.md --out /tmp/songs-test-lib --max-gb 0.02
   ```

   Expect a few downloads into A, each followed by a "linked" line for B, then "Stopping". A second identical run must report 0 new tracks, and `ls -li /tmp/songs-test-lib/*` must show the same inode for a song in A and in B.
4. Tags and cover: `ffprobe -v error -show_entries format_tags=title,artist,album:stream=codec_name,width,height -of compact <file>.mp3`
5. Clean up: `rm -rf /tmp/songs-test.md /tmp/songs-test-lib`

## Design decisions

- **ytmusicapi lists, yt-dlp downloads.** The youtube.com view of these playlists hides some songs that YouTube Music lists (they download fine) and only has raw video titles such as "Bogan - Senthoora Video | Jayam Ravi, Hansika | Imman". YouTube Music gives availability plus clean title, artists and album.
- **YouTube Music metadata reaches yt-dlp through `extra_info`** as `meta_title`, `meta_artist` and `meta_album`. yt-dlp's FFmpegMetadata postprocessor lets `meta_*` fields override the ID3 tags, and the output template uses the same fields for the file name.
- **Round-robin across folders**, so the size limit trims every folder a little instead of leaving the last folders empty.
- **Size limit.** Before each download the size is estimated as duration × bitrate / 8 + 256 KB (cover and tags take about 50 KB, so this errs high) and compared with the usage so far. Usage is measured once at start, counting unique inodes so hard links count once, then increased by the real size of each new file. GB means 10^9 bytes, as in Finder.
- **Duplicates** are matched by YouTube video id across folders and hard-linked. The same song under two different video ids (official audio and music video, say) is downloaded twice.
- **`.index.json`** is `{video_id: [relative paths]}`, rewritten atomically after every song, so Ctrl+C or a crash loses at most the song in progress. Do not delete it: without it, songs shared between folders are downloaded again and the size count runs high.
- **`windowsfilenames`** keeps names valid on FAT/exFAT drives and phones. **`trim_file_name: 70`** keeps Tamil-script names (3 bytes per character in UTF-8) under the 255-byte file name limit.
- **5 to 10 s sleep between downloads** keeps a signed-out session under YouTube's hourly limit (around 300 videos an hour). Extra "Sleeping N seconds as required by the site" waits are YouTube's own ad wait for the TV client and are normal.
- **Stop after 10 failures in a row**, which almost always means rate limiting; carrying on would only prolong the block.

## Traps found so far

- yt-dlp output templates cannot contain `)` inside a field's replacement text: `%(album& ({})|)s` raises KeyError. That is why names use ` - <album>` instead of `(<album>)`.
- yt-dlp only enables Deno by default. Node has to be listed in `js_runtimes` (download.py does this) and must be version 22 or newer.
- FFmpegThumbnailsConvertor skips thumbnails that are already JPEG, so the square crop only applies to WebP thumbnails, which is what YouTube serves almost always.
- ytmusicapi raises KeyError, not YTMusicError, for a playlist that does not exist. download.py catches both.
- yt-dlp's own playlist extraction includes unavailable videos (it reloads with "show unavailable videos"); `compat_opts: ['no-youtube-unavailable-videos']` turns that off. Not needed now that listing uses ytmusicapi, but worth knowing for any yt-dlp listing.
- A one-off HTTP 403 on the first media request has been seen; rerunning fixes it.
- Some music videos list the label channel as the artist (for example SonyMusicSouthVEVO) and some labels pad titles ("Innisai Paadivarum (Language: Tamil; Film: ...)"). That is YouTube Music's data and is kept as is.

## Finding more playlists

Official YouTube Music playlists have IDs starting with `RDCLAK5uy_` and the author "YouTube Music". To search:

```sh
.venv/bin/python -c "
from ytmusicapi import YTMusic
for r in YTMusic().search('Presenting Kamal Haasan', filter='featured_playlists'):
    print(r['title'], '|', r.get('author'), '|', 'https://music.youtube.com/playlist?list=' + r['browseId'][2:])
"
```

Search terms that turn up Tamil playlists: "Presenting <artist> (Tamil)", "'90s Kollywood", "10s Kollywood Romance Hits", "<mood>: Tamil", "Tamil Hits <year>". If an artist has no curated playlist (as with Deva), use the top songs list from their artist page: find the channel id with `YTMusic().search('<artist>', filter='artists')[0]['browseId']` (add a hint for common names, Deva needed 'Deva music director'), then take `YTMusic().get_artist(<channel id>)['songs']['browseId']` without the leading `VL`.

To refresh the song counts in playlists.md (prints DIFF where a count changed):

```sh
.venv/bin/python - <<'EOF'
import re
from ytmusicapi import YTMusic
yt = YTMusic()
for pid, count in re.findall(r"list=([\w-]+)\) - (\d+) songs", open("playlists.md", encoding="utf-8").read()):
    p = yt.get_playlist(pid, limit=None)
    n = sum(1 for t in p["tracks"] if t.get("isAvailable") and t.get("videoId"))
    print("ok  " if n == int(count) else "DIFF", pid, count, "->", n, p["title"])
EOF
```

## Known limitations and ideas

- Hard links need APFS, HFS+ or ext4; with `--out` on exFAT/FAT the run fails at the first duplicate.
- Failed songs are only retried on the next run, not within the same run.
- No track numbers or album artist tags; music apps group by album (movie) and artist.
- Titles are cut at 70 characters, which can chop long ones mid-word.

## Conventions

- Conventional Commits, header at most 100 characters.
- Never commit `library/` (gigabytes of copyrighted audio) or `.venv/`.
- In docs, comments and commit messages use plain hyphens, commas or colons, never em/en dashes or double hyphens as separators.
- Only commit, push or start the full download when the user asks.
