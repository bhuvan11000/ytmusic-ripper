# youtube-music ripper

Rip audio from YouTube videos, playlists, and YouTube Music albums with `rip.py` (powered by `yt-dlp` + `ffmpeg`).


## Setup

Requirements: Python 3, `ffmpeg`.

```bash
# 1. Create venv (already done in this folder)
python3 -m venv .venv

# 2. Install packages into .venv only
./.venv/bin/pip install -r requirements.txt

# 3. Check ffmpeg (required for mp3/m4a conversion + --whole merging)
ffmpeg -version
# Ubuntu/Debian if missing: sudo apt install ffmpeg
```

## Usage

Always run with the venv Python:

```bash
./.venv/bin/python rip.py <URL-or-search> [URL-or-search ...] [options]
```

Bare words are treated as a YouTube search (no URL needed):

```bash
./.venv/bin/python rip.py "lofi hip hop mix"
```

### Examples

```bash
# Single video -> downloads/<title> [id].mp3
./.venv/bin/python rip.py "https://www.youtube.com/watch?v=VIDEO_ID"

# Full playlist / album -> downloads/<Playlist Title>/01 - <title>.mp3, etc.
./.venv/bin/python rip.py "https://www.youtube.com/playlist?list=PLAYLIST_ID"

# YouTube Music album, custom folder + format
./.venv/bin/python rip.py "https://music.youtube.com/playlist?list=ALBUM_ID" --album-name "My Album" -f m4a

# Merge whole album/playlist into ONE file (keeps per-track files too)
./.venv/bin/python rip.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" --whole

# Merge whole album into ONE file only (deletes per-track files after)
./.venv/bin/python rip.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" --whole-only

# Only one video even if the URL contains a playlist (?v=...&list=...)
./.venv/bin/python rip.py "https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID" --no-playlist

# Partial playlist, different folder, quiet
./.venv/bin/python rip.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" --start 3 --end 7 -o ~/Music --quiet

# No URL needed — search YouTube and rip the top result
./.venv/bin/python rip.py "C418 Sweden minecraft"

# Search and rip the top 3 results into a "search - ..." folder
./.venv/bin/python rip.py "synthwave mix" --search 3

# Disable automatic retries (single pass only)
./.venv/bin/python rip.py "https://www.youtube.com/playlist?list=PLAYLIST_ID" --retry 0
```

## Options

| Flag | Default | What it does |
| ---- | ------- | ------------ |
| `-o, --out` | `downloads` | Output directory |
| `-f, --format` | `mp3` | `mp3`, `m4a`, `aac`, `opus`, `vorbis`, `flac`, `wav`, `best` |
| `-q, --quality` | `0` | Lossy quality (`0`=best … `10`=worst; bitrate like `192K` also works) |
| `-s, --search` | `1` | Top results to rip per bare search term |
| `--retry` | `2` | Extra passes over missing/failed tracks, with backoff (`0` = single pass) |
| `--retry-wait` | `15` | Base wait in seconds between retry passes (× attempt number) |
| `--album-name` | playlist title | Force album folder name |
| `--whole` | off | Also merge playlist/album into one `(full album)` file |
| `--whole-only` | off | Keep only the merged file, delete per-track files |
| `--no-playlist` | off | Download single video only |
| `--start / --end` | all | Playlist slice (1-based, inclusive) |
| `--jobs` | `4` | Concurrent fragment downloads |
| `--cookies` | none | Path to `cookies.txt` for age-restricted/private videos |
| `--no-embed-thumbnail` | embed | Skip cover art |
| `--no-embed-metadata` | embed | Skip metadata/chapters |
| `--quiet` | off | Less output |

Full help: `./.venv/bin/python rip.py --help`

## Output layout

```text
downloads/
  Some Video [abc123].mp3                  # single video
  Some Playlist/                           # playlist / album as a whole
    01 - Track One [id1].mp3
    02 - Track Two [id2].mp3
    Some Playlist (full album).mp3         # only with --whole / --whole-only
  search - lofi hip hop mix/               # bare search terms land here too
```

Cover art and metadata/chapters are embedded by default. Track numbers aren't provided by YouTube, so the script copies each file's `NN -` filename prefix into its `tracknumber` tag (mp3/m4a/flac/opus/ogg) — album tracks then sort correctly in players like Navidrome. Pass `--no-embed-metadata` to skip all tagging.

## Troubleshooting

- `ERROR: ffmpeg not found` → install ffmpeg (`sudo apt install ffmpeg`).
- `Sign in to confirm you're not a bot` / age-restricted video → export a `cookies.txt` from your browser (e.g. with the "Get cookies.txt LOCALLY" extension) and pass `--cookies cookies.txt`.
- Nothing downloaded → the video may be private/deleted/region-blocked; remove `--quiet` to see per-track warnings.
- Fewer tracks than expected (e.g. 7 of 24) → usually transient YouTube throttling/bot-checks on some tracks. The script retries missing tracks automatically (`--retry`, default 2 extra passes with backoff) and lists anything still missing at the end with `✗`. If tracks remain missing, wait a while and re-run (already-downloaded tracks are skipped), or pass `--cookies cookies.txt`.
- `No supported JavaScript runtime` warning → the script auto-uses `deno` or `node` if found on `PATH`. Without one, yt-dlp may miss formats and some tracks can fail; install deno (`https://deno.com`) or nodejs to avoid this.
