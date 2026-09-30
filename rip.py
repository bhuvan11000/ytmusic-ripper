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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import sanitize_filename
except ImportError:
    sys.exit("ERROR: yt-dlp not found. Run: ./.venv/bin/pip install yt-dlp mutagen")


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit("ERROR: ffmpeg not found. Install it (e.g. sudo apt install ffmpeg) — yt-dlp needs it for mp3/m4a conversion.")


def pick_js_runtime() -> dict | None:
    """Return a js_runtimes dict for yt-dlp, preferring deno, falling back to node.

    Without a JS runtime yt-dlp warns that "some formats may be missing",
    which makes some tracks fail while others succeed.
    """
    if shutil.which("deno"):
        return {"deno": {}}
    if shutil.which("node"):
        return {"node": {}}
    return None


SEARCH_PREFIX_RE = re.compile(r"^\w+search\d*:", re.IGNORECASE)

AUDIO_EXTS = ("mp3", "m4a", "aac", "opus", "ogg", "vorbis", "flac", "wav", "webm", "mp4")


def normalize_url(raw: str, args: argparse.Namespace) -> tuple[str, str | None]:
    """Return (url_to_download, search_term_or_None).

    Bare words (no URL scheme / search prefix) become a YouTube search for the
    top --search results, so `rip.py never gonna give you up` just works.
    """
    raw = raw.strip()
    if "://" in raw or SEARCH_PREFIX_RE.match(raw):
        return raw, None
    return f"ytsearch{args.search}:{raw}", raw


def probe_url(url: str, args: argparse.Namespace) -> tuple[str | None, list[tuple[str, str | None]] | None]:
    """Lightweight probe: return (album_folder or None, [(video_id, title)] or None).

    Playlists/albums get their own folder (from --album-name or the playlist
    title). Singles return None (= top level of the output dir) with one entry.
    Returns (None, None) if probing itself fails — the download still proceeds.
    """
    probe_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "ignoreerrors": True,
    }
    if args.no_playlist:
        probe_opts["noplaylist"] = True
    if args.playlist_start is not None:
        probe_opts["playliststart"] = args.playlist_start
    if args.playlist_end is not None:
        probe_opts["playlistend"] = args.playlist_end
    if args.cookies:
        probe_opts["cookiefile"] = str(args.cookies)
    try:
        with YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None, None
    if not info:
        return None, None
    if info.get("_type") == "playlist" and not args.no_playlist:
        entries = [
            (e.get("id", ""), e.get("title"))
            for e in (info.get("entries") or [])
            if e and e.get("id")
        ]
        folder = sanitize_filename(args.album_name or info.get("title") or "playlist")
        return folder, entries
    if info.get("id"):
        return None, [(info["id"], info.get("title"))]
    return None, None


def find_missing_tracks(
    target_dir: Path, wanted: list[tuple[str, str | None]], fmt: str
) -> list[tuple[str, str | None]]:
    """Which wanted (video_id, title) pairs have no audio file on disk yet."""
    exts = (fmt,) if fmt != "best" else AUDIO_EXTS
    have = {p.name for p in target_dir.rglob("*") if p.is_file()}
    return [
        (vid, title)
        for vid, title in wanted
        if not any(name.endswith(f"[{vid}].{ext}") for ext in exts for name in have)
    ]


def build_opts(args: argparse.Namespace, download_dir: Path, subfolder: str | None = None) -> dict:
    if subfolder:
        outtmpl = str(download_dir / subfolder / "%(playlist_index)02d - %(title)s [%(id)s].%(ext)s")
    else:
        outtmpl = str(download_dir / "%(title)s [%(id)s].%(ext)s")

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
        # Re-running the same command skips tracks already on disk,
        # so missing/failed tracks can be retried without re-downloading.
        "nooverwrites": True,
    }

    js_runtime = pick_js_runtime()
    if js_runtime:
        opts["js_runtimes"] = js_runtime

    if args.playlist_start is not None:
        opts["playliststart"] = args.playlist_start
    if args.playlist_end is not None:
        opts["playlistend"] = args.playlist_end
    if args.cookies:
        opts["cookiefile"] = str(args.cookies)
    if args.user_agent:
        opts["user_agent"] = args.user_agent

    return opts


TRACK_PREFIX_RE = re.compile(r"^(\d+)\s*-\s*")


def tag_track_numbers(files: list[Path]) -> tuple[int, int]:
    """Write NN filename prefixes (e.g. '09 - Title.mp3') into tracknumber tags.

    YouTube provides no track numbers, so yt-dlp can't embed them — without
    this, players (e.g. Navidrome) order album tracks alphabetically.
    Returns (tagged, failed). Supports mp3/m4a/flac/opus/ogg.
    """
    from mutagen.easyid3 import EasyID3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis

    tagged, failed = 0, 0
    for f in files:
        m = TRACK_PREFIX_RE.match(f.name)
        if not m:
            continue  # singles have no NN prefix — nothing to write
        num = int(m.group(1))
        try:
            ext = f.suffix.lower()
            if ext == ".mp3":
                audio = EasyID3(str(f))
                if audio.get("tracknumber") == [str(num)]:
                    continue
                audio["tracknumber"] = str(num)
                audio.save()
            elif ext in (".m4a", ".aac"):
                audio = MP4(str(f))
                total = audio.tags.get("trkn", [(0, 0)])[0][1] if audio.tags else 0
                if audio.tags and audio.tags.get("trkn") == [(num, total)]:
                    continue
                audio["trkn"] = [(num, total)]
                audio.save()
            elif ext == ".flac":
                audio = FLAC(str(f))
                if audio.get("tracknumber") == [str(num)]:
                    continue
                audio["tracknumber"] = str(num)
                audio.save()
            elif ext in (".opus", ".ogg"):
                audio = (OggOpus if ext == ".opus" else OggVorbis)(str(f))
                if audio.get("tracknumber") == [str(num)]:
                    continue
                audio["tracknumber"] = str(num)
                audio.save()
            else:
                continue
            tagged += 1
        except Exception as e:  # noqa: BLE001 — report and continue with next file
            failed += 1
            print(f"  Could not tag track number for {f.name}: {e}", file=sys.stderr)
    return tagged, failed


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
    p.add_argument("urls", nargs="+", help="URLs and/or bare search terms (e.g. \"lofi hip hop mix\"). Bare terms search YouTube for the top --search results.")
    p.add_argument("-o", "--out", default="downloads", help="Output directory.")
    p.add_argument("-f", "--format", default="mp3", choices=["mp3", "m4a", "aac", "opus", "vorbis", "flac", "wav", "best"], help="Audio container/codec.")
    p.add_argument("-q", "--quality", default="0", help="Audio quality for lossy codecs (0=best … 10=worst for mp3/vorbis; bitrate like 192K also accepted).")
    p.add_argument("-s", "--search", type=int, default=1, help="How many top results to rip per bare search term.")
    p.add_argument("--album-name", default=None, help="Force album folder name for playlists (default: playlist title).")
    p.add_argument("--whole", action="store_true", help="Merge the whole playlist/album into ONE single audio file (in addition to per-track files). Needs ffmpeg.")
    p.add_argument("--whole-only", action="store_true", help="Only keep the merged single file; delete per-track files after merging.")
    p.add_argument("--no-playlist", action="store_true", help="Download only the single video even if URL is a playlist.")
    p.add_argument("--start", dest="playlist_start", type=int, default=None, help="Playlist start index (1-based).")
    p.add_argument("--end", dest="playlist_end", type=int, default=None, help="Playlist end index (inclusive).")
    p.add_argument("--retry", type=int, default=2, help="Extra passes over missing/failed tracks (with backoff). 0 = single pass.")
    p.add_argument("--retry-wait", type=int, default=15, help="Base wait in seconds between retry passes (multiplied by attempt number).")
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

    print(f"Output : {download_dir}")
    print(f"Format : {args.format} (quality {args.quality})")
    print(f"URLs   : {len(args.urls)}")
    if not args.no_playlist:
        print("Mode   : full playlist/album (use --no-playlist for single video only)")

    failures = 0
    still_missing: list[tuple[str, str, str | None]] = []  # (album label, video id, title)
    all_new_files: list[Path] = []
    touched_dirs: set[Path] = set()
    fmt = args.format if args.format != "best" else "best"

    for raw in args.urls:
        url, search_term = normalize_url(raw, args)
        folder, wanted = probe_url(url, args)
        if search_term and folder and not args.album_name:
            folder = sanitize_filename(f"search - {search_term}")
        target = download_dir / folder if folder else download_dir
        if folder:
            touched_dirs.add(target)

        if folder:
            total = f" ({len(wanted)} tracks)" if wanted else ""
            if search_term:
                print(f'\n▶ Search: "{search_term}" → album folder: {folder}{total}\n  {url}')
            else:
                print(f"\n▶ Album: {folder}{total}\n  {url}")
        elif search_term:
            print(f'\n▶ Search: "{search_term}"\n  {url}')
        else:
            print(f"\n▶ {url}")

        missing = list(wanted) if wanted else []
        max_attempts = 1 + max(0, args.retry)
        exts = (fmt,) if fmt != "best" else AUDIO_EXTS
        for attempt in range(max_attempts):
            if attempt > 0:
                wait = args.retry_wait * attempt
                print(f"\nRetry pass {attempt + 1}/{max_attempts}: {len(missing)} track(s) still missing — waiting {wait}s...")
                time.sleep(wait)
            snapshot = set(download_dir.rglob("*"))
            hook_errors: list[str] = []

            def progress_hook(d: dict) -> None:
                # Called per track with status "downloading" / "finished" / "error".
                if d.get("status") == "error":
                    info = d.get("info_dict") or {}
                    hook_errors.append(info.get("title") or info.get("id") or "unknown")

            opts = build_opts(args, download_dir, subfolder=folder)
            opts["progress_hooks"] = [progress_hook]
            try:
                with YoutubeDL(opts) as ydl:
                    ydl.download([url])
            except Exception as e:  # noqa: BLE001 — report and continue with next URL
                failures += 1
                print(f"  FAILED: {e}", file=sys.stderr)

            new_files = collect_audio_files(download_dir, fmt, snapshot)
            if args.format == "best":
                new_files = sorted(
                    (p for p in download_dir.rglob("*") if p.is_file() and p not in snapshot),
                    key=lambda p: p.name,
                )
            all_new_files.extend(new_files)

            if wanted:
                missing = find_missing_tracks(target, wanted, fmt)
            else:
                # Probe gave no track list: retry only if this pass errored
                # or produced nothing new while the target has no audio at all.
                audio_present = any(
                    p.is_file() and p.suffix.lower().lstrip(".") in exts
                    for p in target.rglob("*")
                )
                if hook_errors or (not new_files and not audio_present):
                    missing = [("unknown", None)]
                else:
                    missing = []
            if not missing:
                break

        if folder:
            label = f"Album '{folder}'"
            if wanted:
                print(f"\n{label}: downloaded {len(wanted) - len(missing)} of {len(wanted)} track(s)")
            else:
                print(f"\n{label}: downloaded {len(new_files)} track(s)")
        if missing:
            label = folder or "singles"
            still_missing.extend((label, vid, title) for vid, title in missing)

    new_files = sorted(all_new_files, key=lambda p: p.name)

    if args.embed_metadata:
        # YouTube has no track numbers: copy the NN filename prefix into tags
        # so players (e.g. Navidrome) order album tracks correctly. Covers the
        # whole album folder, so tracks from earlier runs get tagged too.
        exts = (fmt,) if fmt != "best" else AUDIO_EXTS
        to_tag = sorted(
            {
                p
                for d in touched_dirs
                for p in d.rglob("*")
                if p.is_file() and p.suffix.lower().lstrip(".") in exts
            }
        )
        tagged, tag_failed = tag_track_numbers(to_tag)
        if tagged:
            print(f"\nTagged track numbers on {tagged} file(s).")
        failures += tag_failed

    if args.embed_thumbnail:
        # yt-dlp leaves the playlist artwork as "00 - <album> [...].jpg":
        # rename it to cover.jpg. Never overwrites an existing cover.jpg.
        for d in sorted(touched_dirs):
            if (d / "cover.jpg").exists():
                continue
            leftovers = sorted(
                (p for p in d.iterdir() if p.is_file() and p.name.startswith("00 - ")),
                key=lambda p: p.stat().st_size,
                reverse=True,
            )
            if leftovers:
                leftovers[0].rename(d / "cover.jpg")
                print(f"\nRenamed '{leftovers[0].name}' to 'cover.jpg'.")

    if not new_files:
        print("\nNo new audio files were downloaded (check URLs / warnings above).", file=sys.stderr)
        return 2 if (failures or still_missing) else 0

    print(f"\nDone — {len(new_files)} track(s):")
    for f in new_files:
        print(f"  • {f.relative_to(download_dir)}")

    if still_missing:
        print(f"\n{len(still_missing)} track(s) still missing after retries:", file=sys.stderr)
        for label, vid, title in still_missing:
            print(f"  ✗ [{label}] {title or vid} ({vid})", file=sys.stderr)
        print(
            "\nHint: failures are often transient YouTube throttling/bot-checks — re-run later.\n"
            "If failures persist with 'Sign in to confirm you're not a bot', pass --cookies cookies.txt.",
            file=sys.stderr,
        )

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

    return 0 if (failures == 0 and not still_missing) else 2


if __name__ == "__main__":
    raise SystemExit(main())
