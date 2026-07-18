"""Live transcription WebSocket endpoint with VAD-based chunking."""

import asyncio
import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel

from .transcriber import get_model
from .vad import SmartChunker

# Concurrent session limit
MAX_CONCURRENT_SESSIONS = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "4"))
_active_sessions: set[int] = set()
_sessions_lock = asyncio.Lock()

# Binary message size limit (32KB = ~1 second at 16kHz PCM16)
MAX_AUDIO_CHUNK_SIZE = 32 * 1024

# Rate limiting
_connection_attempts: dict[str, list[float]] = defaultdict(list)
MAX_CONNECTIONS_PER_MINUTE = 10
RATE_LIMIT_WINDOW = 60  # seconds


@dataclass
class TranscriptionSession:
    """Manages state for a single live transcription session."""
    
    websocket: WebSocket
    language: str = "auto"
    sample_rate: int = 16000
    
    # Smart chunker with VAD
    chunker: SmartChunker = field(default_factory=lambda: SmartChunker(
        sample_rate=16000,
        min_chunk_duration=1.0,
        max_chunk_duration=10.0,
        vad_threshold=0.5,
    ))
    
    # State
    is_active: bool = True
    total_audio_duration: float = 0.0
    detected_language: str | None = None
    
    # Deduplication: store recent transcript endings to avoid repeats
    recent_texts: list[str] = field(default_factory=list)
    max_recent_texts: int = 5
    
    def pcm16_to_float32(self, pcm_bytes: bytes) -> np.ndarray:
        """Convert PCM16 bytes to float32 numpy array."""
        return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    
    def add_audio(self, pcm_bytes: bytes) -> list[np.ndarray]:
        """Add audio and return any chunks ready for transcription."""
        audio = self.pcm16_to_float32(pcm_bytes)
        return self.chunker.add_audio(audio)
    
    def flush(self) -> np.ndarray | None:
        """Get remaining audio for final transcription."""
        return self.chunker.flush()
    
    def is_duplicate(self, text: str) -> bool:
        """Check if text is a duplicate of recent transcriptions."""
        text_lower = text.lower().strip()
        
        for recent in self.recent_texts:
            recent_lower = recent.lower().strip()
            
            # Check for exact match
            if text_lower == recent_lower:
                return True
            
            # Check if new text is substring of recent (overlap)
            if text_lower in recent_lower:
                return True
            
            # Check for significant overlap at boundaries
            if len(text_lower) > 10 and len(recent_lower) > 10:
                # Check if end of recent matches start of new
                overlap_len = min(len(text_lower), len(recent_lower)) // 2
                if recent_lower[-overlap_len:] == text_lower[:overlap_len]:
                    return True
        
        return False
    
    def add_to_recent(self, text: str):
        """Add text to recent transcriptions for dedup."""
        self.recent_texts.append(text)
        if len(self.recent_texts) > self.max_recent_texts:
            self.recent_texts.pop(0)


async def transcribe_chunk(
    model: WhisperModel,
    audio: np.ndarray,
    language: str | None,
) -> dict[str, Any] | None:
    """
    Transcribe an audio chunk.
    
    Args:
        model: Whisper model instance
        audio: Float32 audio array
        language: Language code or None for auto-detect
        
    Returns:
        Dict with transcription result or None if no speech
    """
    loop = asyncio.get_event_loop()
    
    def do_transcribe():
        segments, info = model.transcribe(
            audio,
            language=language if language and language != "auto" else None,
            temperature=0.0,
            word_timestamps=False,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=500,
                speech_pad_ms=100,
            ),
        )
        return list(segments), info
    
    segments, info = await loop.run_in_executor(None, do_transcribe)
    
    if not segments:
        return None
    
    # Combine all segment texts
    full_text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
    
    if not full_text:
        return None
    
    return {
        "text": full_text,
        "language": info.language,
        "language_probability": info.language_probability,
    }


async def handle_transcription_session(websocket: WebSocket, language: str = "auto"):
    """
    Main handler for a live transcription WebSocket session.
    
    Protocol:
    - Client sends binary PCM16 audio data (16kHz mono)
    - Client sends JSON control messages: {"type": "start/stop/ping"}
    - Server sends JSON responses: {"type": "interim/final/error/pong"}
    """
    # Get client IP for rate limiting
    client_ip = websocket.client.host if websocket.client else "unknown"
    
    # Rate limit check
    now = time.time()
    attempts = _connection_attempts[client_ip]
    # Clean old attempts
    attempts[:] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
    
    if len(attempts) >= MAX_CONNECTIONS_PER_MINUTE:
        await websocket.accept()
        await websocket.send_json({
            "type": "error",
            "message": "Too many connection attempts. Please wait before reconnecting.",
        })
        await websocket.close(code=1008)
        return
    
    attempts.append(now)
    
    # Check session limit before accepting
    session_id = id(websocket)
    async with _sessions_lock:
        if len(_active_sessions) >= MAX_CONCURRENT_SESSIONS:
            await websocket.accept()
            await websocket.send_json({
                "type": "error",
                "message": f"Server at capacity ({MAX_CONCURRENT_SESSIONS} concurrent sessions). Please try again later.",
            })
            await websocket.close(code=1013)  # Try Again Later
            return
        _active_sessions.add(session_id)
    
    await websocket.accept()
    
    session = TranscriptionSession(
        websocket=websocket,
        language=language,
    )
    
    # Get model reference
    model = get_model()
    
    # Send ready message
    await websocket.send_json({
        "type": "ready",
        "sample_rate": session.sample_rate,
        "vad_enabled": True,
    })
    
    # Queue for audio chunks to process
    chunk_queue: asyncio.Queue[np.ndarray | None] = asyncio.Queue()
    
    async def process_chunks():
        """Background task to process audio chunks."""
        while session.is_active:
            try:
                # Wait for chunk with timeout
                try:
                    chunk = await asyncio.wait_for(chunk_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                
                if chunk is None:
                    # Poison pill - stop processing
                    break
                
                try:
                    # Use detected language if available, otherwise session setting
                    lang = session.detected_language or session.language
                    
                    result = await transcribe_chunk(
                        model=model,
                        audio=chunk,
                        language=lang,
                    )
                    
                    if result:
                        # Update detected language for auto mode
                        if session.language == "auto" and result["language"]:
                            session.detected_language = result["language"]
                        
                        # Check for duplicates
                        if not session.is_duplicate(result["text"]):
                            session.add_to_recent(result["text"])
                            session.total_audio_duration += len(chunk) / session.sample_rate
                            
                            await websocket.send_json({
                                "type": "final",
                                "text": result["text"],
                                "language": result["language"],
                                "timestamp": time.time(),
                                "audio_offset": session.total_audio_duration,
                            })
                            
                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e),
                    })
                    
            except asyncio.CancelledError:
                break
    
    # Start processing task
    process_task = asyncio.create_task(process_chunks())
    
    try:
        # Receive loop
        while session.is_active:
            try:
                message = await websocket.receive()
                
                if message["type"] == "websocket.disconnect":
                    break
                
                if "bytes" in message:
                    # Binary audio data
                    audio_data = message["bytes"]
                    if len(audio_data) > MAX_AUDIO_CHUNK_SIZE:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Audio chunk too large ({len(audio_data)} bytes). Max: {MAX_AUDIO_CHUNK_SIZE}",
                        })
                        continue
                    chunks = session.add_audio(audio_data)
                    for chunk in chunks:
                        await chunk_queue.put(chunk)
                    
                elif "text" in message:
                    # JSON control message
                    try:
                        data = json.loads(message["text"])
                        msg_type = data.get("type")
                        
                        if msg_type == "stop":
                            session.is_active = False
                            
                            # Process remaining audio
                            remaining = session.flush()
                            if remaining is not None:
                                await chunk_queue.put(remaining)
                            
                            # Signal end of processing
                            await chunk_queue.put(None)
                            
                            # Wait for processing to finish
                            await process_task
                            
                            await websocket.send_json({"type": "stopped"})
                            break
                            
                        elif msg_type == "ping":
                            await websocket.send_json({
                                "type": "pong",
                                "timestamp": time.time(),
                                "detected_language": session.detected_language,
                            })
                            
                        elif msg_type == "language":
                            # Allow changing language mid-session
                            session.language = data.get("language", "auto")
                            session.detected_language = None  # Reset detection
                            await websocket.send_json({
                                "type": "language_set",
                                "language": session.language,
                            })
                            
                    except json.JSONDecodeError:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Invalid JSON",
                        })
                        
            except WebSocketDisconnect:
                break
                
    except Exception as e:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception:
            pass
            
    finally:
        session.is_active = False
        await chunk_queue.put(None)  # Signal end
        process_task.cancel()
        try:
            await process_task
        except asyncio.CancelledError:
            pass
        
        # Remove from active sessions
        async with _sessions_lock:
            _active_sessions.discard(session_id)
