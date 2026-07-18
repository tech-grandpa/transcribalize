"""Output formatters for transcription results."""

from typing import Any


def format_timestamp_srt(seconds: float) -> str:
    """Format timestamp for SRT (HH:MM:SS,mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_srt(segments: list[dict[str, Any]]) -> str:
    """
    Format transcription segments as SRT subtitle format.
    
    Args:
        segments: List of segment dicts with 'start', 'end', 'text' keys
        
    Returns:
        SRT formatted string
    """
    lines = []
    for i, seg in enumerate(segments, 1):
        start = format_timestamp_srt(seg["start"])
        end = format_timestamp_srt(seg["end"])
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(seg["text"].strip())
        lines.append("")
    return "\n".join(lines)


def format_markdown(segments: list[dict[str, Any]]) -> str:
    """
    Format transcription as clean markdown text.
    
    Groups segments into paragraphs (split on longer pauses > 2s).
    
    Args:
        segments: List of segment dicts with 'start', 'end', 'text' keys
        
    Returns:
        Clean markdown formatted text
    """
    if not segments:
        return ""
    
    paragraphs = []
    current_paragraph = []
    last_end = 0.0
    
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
            
        # Start new paragraph if there's a gap > 2 seconds
        if current_paragraph and (seg["start"] - last_end) > 2.0:
            paragraphs.append(" ".join(current_paragraph))
            current_paragraph = []
        
        current_paragraph.append(text)
        last_end = seg["end"]
    
    # Don't forget the last paragraph
    if current_paragraph:
        paragraphs.append(" ".join(current_paragraph))
    
    return "\n\n".join(paragraphs)


def format_text(segments: list[dict[str, Any]]) -> str:
    """
    Format transcription as plain text.
    
    Args:
        segments: List of segment dicts with 'text' key
        
    Returns:
        Plain text with all segments joined
    """
    return " ".join(seg["text"].strip() for seg in segments if seg["text"].strip())


def format_json(segments: list[dict[str, Any]], info: dict[str, Any]) -> dict[str, Any]:
    """
    Format transcription as detailed JSON.
    
    Args:
        segments: List of segment dicts
        info: Transcription info dict with language, duration, etc.
        
    Returns:
        JSON-serializable dict with full transcription data
    """
    return {
        "language": info.get("language", "unknown"),
        "language_probability": info.get("language_probability"),
        "duration": info.get("duration"),
        "text": format_text(segments),
        "segments": [
            {
                "id": i,
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip(),
            }
            for i, seg in enumerate(segments)
        ],
    }
