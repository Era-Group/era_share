# -*- coding: utf-8 -*-
"""Private recording preparation; never creates a Drive sharing permission."""
import os
import shutil
import subprocess


class MediaPreparationError(Exception):
    pass


def prepare_audio(source_path, directory):
    """Losslessly extract AAC when ffmpeg exists, otherwise return the MP4.

    Google Meet normally stores AAC in its MP4. Stream-copying that track keeps
    native quality and greatly reduces upload size. Direct MP4 remains an
    officially supported AssemblyAI input and is the safe no-system-dependency
    fallback.
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        return source_path
    output = os.path.join(directory, 'audio.m4a')
    try:
        result = subprocess.run(
            [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error',
             '-i', source_path, '-map', '0:a:0', '-vn', '-c:a', 'copy',
             '-movflags', '+faststart', '-y', output],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaPreparationError("Audio extraction failed: %s" % exc) from exc
    if result.returncode == 0 and os.path.isfile(output) and os.path.getsize(output):
        os.chmod(output, 0o600)
        return output
    # A codec/container ffmpeg cannot copy is still accepted inside the MP4.
    return source_path
