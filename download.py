#!/usr/bin/env python3
"""Download the YouTube Music playlists in playlists.md as tagged MP3s, one folder per heading.

    .venv/bin/python download.py --dry-run   # show what would be downloaded and the final size
    .venv/bin/python download.py             # download, stopping before the library passes 10 GB

Needs ffmpeg, plus Node.js 22+ or Deno on PATH. Rerunning resumes where the last run stopped.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import os
import re
import shutil
import sys
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicError

HERE = Path(__file__).resolve().parent
GB = 1000**3
INDEX_FILE = '.index.json'
TEMP_DIR = '.tmp'
COVER_AND_TAGS_BYTES = 256 * 1024
UNKNOWN_DURATION_S = 600
MAX_FAILURES_IN_A_ROW = 10

HEADING = re.compile(r'^##\s+(.+?)\s*$')
PLAYLIST_ID = re.compile(r'[?&]list=([\w-]+)')
OST_SUFFIX = re.compile(r'\s*\((?:Original )?Motion Picture Soundtrack\)$')


def read_folders(doc: Path) -> dict[str, list[str]]:
    """Map every '## heading' in the doc to the playlist IDs linked under it."""
    folders: dict[str, list[str]] = {}
    folder = None
    for line in doc.read_text(encoding='utf-8').splitlines():
        if heading := HEADING.match(line):
            folder = heading[1]
            folders.setdefault(folder, [])
        elif line.startswith('# '):
            folder = None
        elif folder and (link := PLAYLIST_ID.search(line)):
            folders[folder].append(link[1])
    return {name: ids for name, ids in folders.items() if ids}


def list_tracks(folders: dict[str, list[str]]) -> dict[str, list[dict]]:
    """Fetch the playable tracks of each folder's playlists, with YouTube Music's song metadata."""
    ytmusic = YTMusic()
    tracks: dict[str, list[dict]] = {}
    for folder, playlist_ids in folders.items():
        unique: dict[str, dict] = {}
        for playlist_id in playlist_ids:
            try:
                playlist = ytmusic.get_playlist(playlist_id, limit=None)
            except (KeyError, YTMusicError):
                print(f'{folder}: could not load playlist {playlist_id}, skipping it')
                continue
            playable = [t for t in playlist['tracks'] if t.get('isAvailable') and t.get('videoId')]
            print(f'{folder}: {playlist["title"]} - {len(playable)} tracks')
            for t in playable:
                album = (t.get('album') or {}).get('name')
                unique.setdefault(t['videoId'], {
                    'id': t['videoId'],
                    'title': t['title'],
                    'artist': ', '.join(a['name'] for a in t.get('artists') or []) or None,
                    'album': OST_SUFFIX.sub('', album) if album else None,
                    'duration': t.get('duration_seconds'),
                })
        tracks[folder] = list(unique.values())
    return tracks


def interleave(tracks: dict[str, list[dict]]) -> list[tuple[str, dict]]:
    """Round-robin across folders, so hitting the size cap still leaves every folder its top songs."""
    rounds = itertools.zip_longest(*([(folder, t) for t in folder_tracks] for folder, folder_tracks in tracks.items()))
    return [item for row in rounds for item in row if item]


def disk_usage(root: Path) -> int:
    """Bytes used under root, counting hard-linked files once."""
    sizes = {}
    for path in root.rglob('*'):
        if path.is_file():
            stat = path.stat()
            sizes[stat.st_ino] = stat.st_size
    return sum(sizes.values())


def estimate_bytes(track: dict, bitrate_kbps: int) -> int:
    seconds = track.get('duration') or UNKNOWN_DURATION_S
    return int(seconds * bitrate_kbps * 1000 / 8) + COVER_AND_TAGS_BYTES


def save_index(path: Path, index: dict[str, list[str]]) -> None:
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def make_downloader(folder_dir: Path, temp_dir: Path, bitrate_kbps: int) -> YoutubeDL:
    return YoutubeDL({
        'js_runtimes': {'deno': {}, 'node': {}},
        'quiet': True,
        'format': 'bestaudio/best',
        'paths': {'home': str(folder_dir), 'temp': str(temp_dir)},
        'outtmpl': '%(meta_title)s%(meta_album& - {}|)s.%(ext)s',
        # Tamil script takes 3 bytes per character, this keeps names under the 255-byte limit
        'trim_file_name': 70,
        'windowsfilenames': True,
        'final_ext': 'mp3',
        'writethumbnail': True,
        'postprocessors': [
            {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg', 'when': 'before_dl'},
            {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': str(bitrate_kbps)},
            {'key': 'FFmpegMetadata'},
            {'key': 'EmbedThumbnail'},
        ],
        # crops the 16:9 video thumbnail to a square cover
        'postprocessor_args': {
            'thumbnailsconvertor+ffmpeg_o': ['-c:v', 'mjpeg', '-vf', "crop='min(iw,ih)':'min(iw,ih)'"],
        },
        # stays under YouTube's hourly limit for signed-out downloads
        'sleep_interval': 5,
        'max_sleep_interval': 10,
        'noprogress': True,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description='Download the playlists in playlists.md as MP3s.')
    parser.add_argument('--doc', type=Path, default=HERE / 'playlists.md', help='playlist doc (default: %(default)s)')
    parser.add_argument('--out', type=Path, default=HERE / 'library', help='library folder (default: %(default)s)')
    parser.add_argument('--max-gb', type=float, default=10.0, help='library size limit in GB (default: %(default)s)')
    parser.add_argument('--bitrate', type=int, default=192, choices=(128, 192, 256, 320),
                        help='MP3 bitrate in kbps (default: %(default)s)')
    parser.add_argument('--dry-run', action='store_true', help='show what would be downloaded, download nothing')
    args = parser.parse_args()
    if not args.dry_run and not shutil.which('ffmpeg'):
        sys.exit('ffmpeg not found, install it first (brew install ffmpeg)')

    out = args.out.expanduser().resolve()
    cap = int(args.max_gb * GB)
    index_path = out / INDEX_FILE
    index: dict[str, list[str]] = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    used = disk_usage(out)

    queue = interleave(list_tracks(read_folders(args.doc)))
    print(f'\n{len(queue)} tracks queued, library uses {used / GB:.2f} of {args.max_gb:g} GB\n')

    planned: set[str] = set()
    downloaded = linked = failed = failures_in_a_row = 0
    with contextlib.ExitStack() as stack:
        downloaders: dict[str, YoutubeDL] = {}
        for n, (folder, track) in enumerate(queue, 1):
            vid = track['id']
            copies = [p for p in index.get(vid, []) if (out / p).is_file()]
            if any(Path(p).parent == Path(folder) for p in copies):
                continue
            if copies or vid in planned:
                if not args.dry_run:
                    src = out / copies[0]
                    dst = out / folder / src.name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        os.link(src, dst)
                    index[vid] = [*copies, dst.relative_to(out).as_posix()]
                    save_index(index_path, index)
                    print(f'[{n}/{len(queue)}] {folder} / {dst.name}  (linked, no extra space)')
                linked += 1
                continue

            size = estimate_bytes(track, args.bitrate)
            if used + size > cap:
                print(f'Stopping: the next track would take the library past {args.max_gb:g} GB '
                      f'({len(queue) - n + 1} queued tracks left).')
                break
            if args.dry_run:
                planned.add(vid)
                used += size
                downloaded += 1
                continue

            if folder not in downloaders:
                downloaders[folder] = stack.enter_context(make_downloader(out / folder, out / TEMP_DIR, args.bitrate))
            # meta_* fields become the file name and ID3 tags in place of the raw video title
            song = {'meta_title': track['title'], 'meta_artist': track['artist'], 'meta_album': track['album']}
            try:
                info = downloaders[folder].extract_info(f'https://www.youtube.com/watch?v={vid}', extra_info=song)
            except DownloadError:
                failed += 1
                failures_in_a_row += 1
                print(f'[{n}/{len(queue)}] failed: {folder} / {track.get("title")}')
                if failures_in_a_row == MAX_FAILURES_IN_A_ROW:
                    print('Too many failures in a row, YouTube is probably rate-limiting. Rerun later to resume.')
                    break
                continue
            failures_in_a_row = 0
            mp3 = Path(info['requested_downloads'][0]['filepath'])
            used += mp3.stat().st_size
            index[vid] = [mp3.relative_to(out).as_posix()]
            save_index(index_path, index)
            downloaded += 1
            print(f'[{n}/{len(queue)}] {folder} / {mp3.name}  ({used / GB:.2f} GB)')

    summary = f'{downloaded} new tracks, {linked} hard-linked duplicates, {failed} failed'
    print(f'\n{"Plan" if args.dry_run else "Done"}: {summary}. '
          f'Library: {used / GB:.2f} of {args.max_gb:g} GB in {out}')
    if failed:
        print('Rerun to retry the failed tracks.')


if __name__ == '__main__':
    main()
