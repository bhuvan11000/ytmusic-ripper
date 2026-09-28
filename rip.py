#!/usr/bin/env python3
"""
rip.py — Rip audio from YouTube videos / playlists / YouTube Music albums.

Uses yt-dlp (installed in .venv) + ffmpeg.

Usage:
    ./.venv/bin/python rip.py <URL> [URL ...] [options]
    ./.venv/bin/python rip.py "https://www.youtube.com/watch?v=..." -o downloads
    ./.venv/bin/python rip.py "https://www.youtube.com/playlist?list=..." --format mp3
    ./.venv/bin/python rip.py "https://music.youtube.com/playlist?list=..." --whole --album-name "My Album"

    Playlists/albums download as a folder (one file per track) by default.
    Pass --whole to also merge the whole album/playlist into ONE audio file.

Requires: .venv with yt-dlp, system ffmpeg.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from yt_dlp import YoutubeDL
except ImportError:
    sys.exit("ERROR: yt-dlp not found. Run: ./.venv/bin/pip install yt-dlp mutagen")


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg not found. Install it (e.g. sudo apt install ffmpeg) — yt-dlp needs it for mp3/m4a conversion.")


def build_opts(args: argparse.Namespace, download_dir: Path) -> dict:
    outtmpl = {
        "default": str(download_dir / "%(title)s [%(id)s].%(ext)s"),
        "playlist": str(download_dir / "%(playlist_title)s" / "%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"),
    }

    postprocessors = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": args.format,
            **({"preferredquality": str(args.quality)} if args.format in ("mp3", "m4a", "aac", "opus", "vorbis") else {}),
        },
    ]
    if args.embed_metadata:
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
    if args.embed_thumbnail:
        postprocessors.append({"key": "EmbedThumbnail"})
        # EmbedThumbnail requires atomicparsley fallback off; modern ffmpeg is fine.
        # yt-dlp will convert thumbnail to jpg automatically.

    opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": postprocessors,
        "writethumbnail": args.embed_thumbnail,
        "writeinfojson": False,
        "writesubtitles": False,
        "ignoreerrors": True,
        "noplaylist": args.no_playlist,  # False by default -> whole playlist/album
        "yes_playlist": not args.no_playlist,
        "continuedl": True,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": args.jobs,
        "addmetadata": True,
        "embed_infojson": False,
        "quiet": args.quiet,
        "no_warnings": False,
        "progress": not args.quiet,
        "nocheckcertificate": False,
        "prefer_ffmpeg": True,
        "keepvideo": False,
    }

    if args.playlist_start is not None:
        opts["playliststart"] = args.playlist_start
    if args.playlist_end is not None:
        opts["playlistend"] = args.playlist_end
    if args.cookies:
        opts["cookiefile"] = str(args.cookies)
    if args.user_agent:
        opts["user_agent"] = args.user_agent

    return opts


def merge_to_single_file(track_files: list[Path], output_file: Path, fmt: str) -> Path:
    """Concat downloaded tracks (in order) into one file via ffmpeg. Re-encodes to keep it simple/robust."""
    output_file = output_file.with_suffix(f".{fmt}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        listfile = Path(f.name)
        for t in track_files:
            # ffmpeg concat demuxer: file '<path>' (escape single quotes)
            safe = str(t.resolve()).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")

    # -safe 0 allows absolute paths. Re-encode (not -c copy) so mixed codecs/track params merge cleanly.
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c:a", "libmp3lame" if fmt == "mp3" else "aac" if fmt in ("m4a", "aac") else "copy" if fmt in ("opus",) else "pcm_s16le", str(output_file)]
    # For opus/flac/wav copy can fail across files; prefer re-encode generically:
    if fmt in ("opus", "vorbis", "flac", "wav", "best"):
        # let ffmpeg pick encoder from extension
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), str(output_file)]

    print(f"\nMerging {len(track_files)} tracks into:\n  {output_file}")
    r = subprocess.run(cmd, capture_output=False)
    listfile.unlink(missing_ok=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: ffmpeg merge failed (exit {r.returncode})")
    return output_file


def collect_audio_files(root: Path, fmt: str, before: set[Path]) -> list[Path]:
    exts = {fmt, "mp3", "m4a", "opus", "flac", "wav", "aac", "ogg", "webm", "mp4", "m4a"} if fmt == "best" else {fmt}
    found = sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower().lstrip(".") in exts and p not in before),
        key=lambda p: p.name,
    )
    return found


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Rip audio from YouTube video / playlist / YouTube Music album. Playlists download as a whole album folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("urls", nargs="+", help="One or more YouTube video / playlist / music.youtube.com album URLs (or ytsearch queries).")
    p.add_argument("-o", "--out", default="downloads", help="Output directory.")
    p.add_argument("-f", "--format", default="mp3", choices=["mp3", "m4a", "aac", "opus", "vorbis", "flac", "wav", "best"], help="Audio container/codec.")
    p.add_argument("-q", "--quality", default="0", help="Audio quality for lossy codecs (0=best … 10=worst for mp3/vorbis; bitrate like 192K also accepted).")
    p.add_argument("--album-name", default=None, help="Force album folder name for playlists (default: playlist title).")
    p.add_argument("--whole", action="store_true", help="Merge the whole playlist/album into ONE single audio file (in addition to per-track files). Needs ffmpeg.")
    p.add_argument("--whole-only", action="store_true", help="Only keep the merged single file; delete per-track files after merging.")
    p.add_argument("--no-playlist", action="store_true", help="Download only the single video even if URL is a playlist.")
    p.add_argument("--start", dest="playlist_start", type=int, default=None, help="Playlist start index (1-based).")
    p.add_argument("--end", dest="playlist_end", type=int, default=None, help="Playlist end index (inclusive).")
    p.add_argument("--jobs", type=int, default=4, help="Concurrent fragment downloads.")
    p.add_argument("--cookies", default=None, help="Path to cookies.txt (needed for age-restricted / private videos).")
    p.add_argument("--user-agent", default=None, help="Custom User-Agent string.")
    p.add_argument("--no-embed-thumbnail", dest="embed_thumbnail", action="store_false", help="Don't embed cover art.")
    p.add_argument("--no-embed-metadata", dest="embed_metadata", action="store_false", help="Don't embed metadata/chapters.")
    p.add_argument("--quiet", action="store_true", help="Less output.")
    p.set_defaults(embed_thumbnail=True, embed_metadata=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    check_ffmpeg()

    download_dir = Path(args.out).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    before = set(download_dir.rglob("*"))

    # If --album-name given and downloading a playlist, pre-route output into that folder.
    # We do this by post-hoc rename: simpler = download normally, then rename playlist folder.
    opts = build_opts(args, download_dir)

    print(f"Output : {download_dir}")
    print(f"Format : {args.format} (quality {args.quality})")
    print(f"URLs   : {len(args.urls)}")
    if not args.no_playlist:
        print("Mode   : full playlist/album (use --no-playlist for single video only)")

    failures = 0
    with YoutubeDL(opts) as ydl:
        for url in args.urls:
            try:
                print(f"\n▶ {url}")
                ydl.download([url])
            except Exception as e:  # noqa: BLE001 — report and continue with next URL
                failures += 1
                print(f"  FAILED: {e}", file=sys.stderr)

    new_files = collect_audio_files(download_dir, args.format if args.format != "best" else "best", before)
    # collect_audio_files with 'best' matches many exts; handle that case
    if args.format == "best":
        new_files = sorted((p for p in download_dir.rglob("*") if p.is_file() and p not in before), key=lambda p: p.name)

    if not new_files:
        print("\nNo new audio files were downloaded (check URLs / warnings above).", file=sys.stderr)
        return 1 if failures else 0

    print(f"\nDone — {len(new_files)} track(s):")
    for f in new_files:
        print(f"  • {f.relative_to(download_dir)}")

    # Optional --album-name rename: if exactly one new top-level folder appeared, rename it.
    if args.album_name:
        top_dirs = {f.relative_to(download_dir).parts[0] for f in new_files if len(f.relative_to(download_dir).parts) > 1}
        if len(top_dirs) == 1:
            src = download_dir / next(iter(top_dirs))
            dst = download_dir / args.album_name
            if src != dst and not dst.exists():
                src.rename(dst)
                print(f"\nRenamed album folder:\n  {src.name} -> {dst.name}")
                new_files = sorted(dst.rglob(f"*.{args.format}") if args.format != "best" else dst.rglob("*"))

    # Optional: merge whole album into one file
    if args.whole or args.whole_only:
        # Group by album folder; singles at top level merge together too.
        from collections import defaultdict

        groups: dict[Path, list[Path]] = defaultdict(list)
        for f in sorted(new_files):
            rel = f.relative_to(download_dir)
            groups[download_dir / rel.parts[0] if len(rel.parts) > 1 else download_dir].append(f)

        for group_dir, tracks in groups.items():
            if len(tracks) < 2:
                print(f"\nSkipping merge for {group_dir.name}: only 1 track.")
                continue
            album = args.album_name or (group_dir.name if group_dir != download_dir else "youtube-rip")
            merged = (group_dir if group_dir != download_dir else download_dir) / f"{album} (full album).{args.format if args.format != 'best' else 'mp3'}"
            merge_to_single_file(tracks, merged, args.format if args.format != "best" else "mp3")
            if args.whole_only:
                for t in tracks:
                    t.unlink(missing_ok=True)
                print("  (per-track files removed — --whole-only)")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
