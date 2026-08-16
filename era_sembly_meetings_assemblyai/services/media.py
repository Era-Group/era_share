# -*- coding: utf-8 -*-
"""Private mono audio preparation; never creates a Drive sharing permission."""
import os
import shutil
import subprocess


class MediaPreparationError(Exception):
    pass


def ffmpeg_executable():
    """Return system ffmpeg or the binary bundled by imageio-ffmpeg."""
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        executable = get_ffmpeg_exe()
    except (ImportError, OSError):
        return None
    return executable if executable and os.path.isfile(executable) else None


def prepare_audio(source_path, directory):
    """Downmix the first audio track to one AAC channel before upload.

    AssemblyAI bills prerecorded multichannel media per channel. Google Meet
    recordings can be stereo even though diarization only needs one mixed
    channel, so uploading the original MP4 can almost double usage. Conversion
    is fail-closed: a recording is never uploaded unless mono preparation
    succeeds.
    """
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        raise MediaPreparationError(
            "Mono audio conversion is unavailable; ffmpeg is required")
    output = os.path.join(directory, 'audio.m4a')
    try:
        result = subprocess.run(
            [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-i', source_path, '-map', '0:a:0', '-vn', '-ac', '1',
             '-c:a', 'aac', '-b:a', '64k',
             '-movflags', '+faststart', '-y', output],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaPreparationError("Audio extraction failed: %s" % exc) from exc
    if result.returncode == 0 and os.path.isfile(output) and os.path.getsize(output):
        os.chmod(output, 0o600)
        return output
    detail = result.stderr.decode(errors='replace').strip()[-500:]
    raise MediaPreparationError(
        "Mono audio conversion failed%s" % (": %s" % detail if detail else ""))
