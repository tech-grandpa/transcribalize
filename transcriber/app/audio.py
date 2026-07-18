"""FFmpeg-based audio extraction and conversion module."""

import subprocess
from pathlib import Path

# Supported input formats (FFmpeg supports many more, but these are common)
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".webm"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".mpeg", ".mpg", ".ts", ".webm"}
SUPPORTED_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS


def is_supported_format(filename: str) -> bool:
    """Check if the file extension is supported."""
    suffix = Path(filename).suffix.lower()
    return suffix in SUPPORTED_EXTENSIONS


def extract_audio(input_path: Path, output_path: Path) -> None:
    """
    Extract/convert any audio/video to 16kHz mono WAV for Whisper.
    
    Uses FFmpeg subprocess for reliability with large files.
    
    Args:
        input_path: Path to input audio/video file
        output_path: Path for output WAV file
        
    Raises:
        RuntimeError: If FFmpeg conversion fails
    """
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-vn",              # No video
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ac", "1",         # Mono
        "-ar", "16000",     # 16kHz (Whisper's native sample rate)
        "-y",               # Overwrite output
        str(output_path)
    ]
    
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed: {result.stderr}")


def get_audio_duration(file_path: Path) -> float | None:
    """
    Get duration of audio/video file in seconds using ffprobe.
    
    Args:
        file_path: Path to media file
        
    Returns:
        Duration in seconds, or None if detection fails
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0 and result.stdout.strip():
        try:
            return float(result.stdout.strip())
        except ValueError:
            return None
    return None
