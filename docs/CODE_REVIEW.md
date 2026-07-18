# Code Review: Live Transcription Feature

**Branch:** `feature/live-transcription`  
**Reviewer:** Jarvis (AI Code Review)  
**Date:** 2026-02-08  
**Files Reviewed:** 7

---

## Summary

The live transcription feature implements real-time speech-to-text via WebSocket with VAD-based smart chunking. Overall, this is a **well-architected implementation** with good separation of concerns, proper async handling, and thoughtful UX features like session persistence and reconnection logic.

**Overall Assessment:** 🟡 **Needs Minor Fixes Before Merge**

The code is production-ready in most areas, but there are a few security and robustness issues that should be addressed before deployment.

---

## Issues Found

### 🔴 Critical (0)

No critical issues found.

---

### 🟠 Major (4)

#### M1. No Binary Message Size Validation (Security)
**File:** `live_transcription.py` (line ~186)

The WebSocket handler accepts binary audio data without validating the message size. A malicious client could send extremely large binary messages to exhaust server memory.

```python
# Current - no validation
if "bytes" in message:
    chunks = session.add_audio(message["bytes"])
```

**Recommendation:**
```python
MAX_AUDIO_CHUNK_SIZE = 32 * 1024  # 32KB = 1 second at 16kHz PCM16

if "bytes" in message:
    audio_data = message["bytes"]
    if len(audio_data) > MAX_AUDIO_CHUNK_SIZE:
        await websocket.send_json({
            "type": "error",
            "message": f"Audio chunk too large ({len(audio_data)} bytes). Max: {MAX_AUDIO_CHUNK_SIZE}",
        })
        continue
    chunks = session.add_audio(audio_data)
```

---

#### M2. VAD Model Loading Without Error Handling (Reliability)
**File:** `vad.py` (lines 12-24)

The Silero VAD model is loaded from `torch.hub` without error handling. If the network is unavailable or the model download fails, the entire service will crash.

```python
# Current - no error handling
def get_vad_model():
    global _vad_model, _vad_utils
    if _vad_model is None:
        model, utils = torch.hub.load(...)  # Can raise various exceptions
```

**Recommendation:**
```python
def get_vad_model():
    global _vad_model, _vad_utils
    
    if _vad_model is None:
        try:
            print("Loading Silero VAD model...")
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=False,
            )
            _vad_model = model
            _vad_utils = utils
            print("Silero VAD model loaded")
        except Exception as e:
            print(f"Failed to load Silero VAD: {e}")
            raise RuntimeError(
                "VAD model initialization failed. "
                "Ensure the model is pre-downloaded or network is available."
            ) from e
    
    return _vad_model, _vad_utils
```

---

#### M3. Potential Memory Growth in Long Sessions (Performance)
**File:** `vad.py` (line 145) and `live_transcription.py`

The `SmartChunker.add_audio()` method uses `np.concatenate()` which creates a new array on every call. For long transcription sessions, this can cause memory fragmentation and GC pressure.

```python
# Current - creates new array each time
self.buffer = np.concatenate([self.buffer, audio])
```

**Recommendation:** Use a pre-allocated ring buffer or deque:
```python
from collections import deque

class SmartChunker:
    def __init__(self, ...):
        # Use deque for O(1) append
        self._audio_chunks: deque[np.ndarray] = deque()
        self._total_samples: int = 0
    
    def add_audio(self, audio: np.ndarray) -> list[np.ndarray]:
        self._audio_chunks.append(audio)
        self._total_samples += len(audio)
        
        # ... chunking logic ...
        
    def _get_buffer(self) -> np.ndarray:
        """Materialize buffer only when needed."""
        if not self._audio_chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(list(self._audio_chunks))
```

---

#### M4. Session Limit Bypass via Reconnection (Security)
**File:** `live_transcription.py` (lines 117-127)

The session tracking uses `id(websocket)` which changes on each connection. A client could rapidly connect/disconnect to bypass the concurrent session limit or DoS the server.

**Recommendation:** Add rate limiting per IP address:
```python
from collections import defaultdict
import time

_connection_attempts: dict[str, list[float]] = defaultdict(list)
MAX_CONNECTIONS_PER_IP = 10
CONNECTION_WINDOW_SECONDS = 60

async def handle_transcription_session(websocket: WebSocket, language: str = "auto"):
    # Get client IP (for rate limiting)
    client_ip = websocket.client.host if websocket.client else "unknown"
    
    # Rate limit check
    now = time.time()
    attempts = _connection_attempts[client_ip]
    attempts[:] = [t for t in attempts if now - t < CONNECTION_WINDOW_SECONDS]
    
    if len(attempts) >= MAX_CONNECTIONS_PER_IP:
        await websocket.close(code=1008, reason="Too many connection attempts")
        return
    
    attempts.append(now)
    # ... rest of handler
```

---

### 🟡 Minor (8)

#### m1. Sample Rate Mismatch Risk
**File:** `live_transcription.py` (lines 25-31)

The `TranscriptionSession` dataclass has a `sample_rate` field that defaults to 16000, but the `chunker` default factory also hardcodes 16000. If someone changes one but not the other, they'll be out of sync.

**Recommendation:** Make chunker use the session's sample_rate:
```python
def __post_init__(self):
    self.chunker = SmartChunker(
        sample_rate=self.sample_rate,
        min_chunk_duration=1.0,
        max_chunk_duration=10.0,
        vad_threshold=0.5,
    )
```

---

#### m2. Duplicate Detection Edge Cases
**File:** `live_transcription.py` (lines 52-70)

The `is_duplicate()` method checks if new text is a substring of recent text, but this could cause false positives for common phrases like "okay" or "thank you".

**Recommendation:** Add minimum length check before substring comparison:
```python
def is_duplicate(self, text: str) -> bool:
    text_lower = text.lower().strip()
    
    # Short phrases are more likely to repeat legitimately
    if len(text_lower) < 15:
        # Only check exact matches for short phrases
        return any(text_lower == r.lower().strip() for r in self.recent_texts)
    
    # ... rest of logic
```

---

#### m3. Missing Type Hints
**File:** `live_transcription.py` (line 93)

The return type of `transcribe_chunk` should be more specific.

```python
# Current
async def transcribe_chunk(...) -> dict[str, Any] | None:

# Better - define a TypedDict
from typing import TypedDict

class TranscriptionResult(TypedDict):
    text: str
    language: str
    language_probability: float

async def transcribe_chunk(...) -> TranscriptionResult | None:
```

---

#### m4. Bare `except` in Cleanup
**File:** `live_transcription.py` (line 221)

Bare `except:` swallows all exceptions including `KeyboardInterrupt` and `SystemExit`.

```python
# Current
except:
    pass

# Better
except Exception:
    pass
```

---

#### m5. AudioWorklet Buffer Allocation on Flush
**File:** `audio-processor.js` (line 32)

Creating a new `Int16Array` on every flush is unnecessary overhead.

```python
# Current
this.buffer = new Int16Array(this.bufferSize);

# Better - reuse the buffer
this.bufferIndex = 0;  // Just reset index
```

Note: The current code transfers the buffer's underlying ArrayBuffer, so this would need a new backing buffer anyway. Consider keeping an object pool of 2 buffers.

---

#### m6. Frontend Session Storage Key Collision
**File:** `live.html` (line ~320)

The localStorage key `'live-transcription-session'` is generic and could collide if multiple transcription services are deployed on the same domain.

**Recommendation:**
```javascript
const STORAGE_KEY = `live-transcription-session-${window.location.pathname}`;
```

---

#### m7. Missing CORS Headers for WebSocket
**File:** `main.py`

The WebSocket endpoint doesn't explicitly handle CORS. While FastAPI handles this for HTTP, WebSocket connections from different origins might fail in some browsers.

**Recommendation:** Add explicit WebSocket origin validation if cross-origin access is needed.

---

#### m8. Console Logging in Production
**File:** `vad.py` (lines 14, 22)

Using `print()` for logging. Should use proper logging module for production.

```python
import logging
logger = logging.getLogger(__name__)

# Replace print() with
logger.info("Loading Silero VAD model...")
```

---

## Recommendations

### Security Hardening
1. **Add input validation** for all WebSocket binary messages (size limits)
2. **Implement rate limiting** per IP to prevent DoS attacks
3. **Add connection timeout** to prevent idle connections from holding resources
4. **Consider authentication** for the WebSocket endpoint if this is a public service

### Performance Improvements
1. **Use ring buffer** instead of `np.concatenate()` for audio buffering
2. **Add connection pooling** for the Whisper model if supporting many concurrent sessions
3. **Consider GPU memory management** - the current approach loads one model that's shared, which is correct
4. **Add backpressure handling** - if transcription is slower than audio arrival, implement dropping or queueing strategy

### Monitoring & Observability
1. **Add structured logging** with session IDs for debugging
2. **Add metrics** (active sessions, transcription latency, error rates)
3. **Add health check** that verifies the Whisper and VAD models are loaded

### Code Quality
1. **Add unit tests** for the VAD and chunking logic
2. **Add integration tests** for the WebSocket protocol
3. **Document the WebSocket protocol** in OpenAPI/AsyncAPI format
4. **Add `__all__`** exports to Python modules

---

## Browser Compatibility Assessment

| Feature | Chrome | Firefox | Safari | Edge |
|---------|--------|---------|--------|------|
| WebSocket | ✅ | ✅ | ✅ | ✅ |
| AudioWorklet | ✅ | ✅ | ✅ (14.1+) | ✅ |
| getUserMedia | ✅ | ✅ | ✅ | ✅ |
| getDisplayMedia + Audio | ✅ | ✅ | ⚠️ Limited | ✅ |
| Int16Array Transfer | ✅ | ✅ | ✅ | ✅ |

**Notes:**
- Safari's `getDisplayMedia` audio support is inconsistent; the "Tab/Window Audio" option may not work
- The code correctly shows an error if no audio track is captured
- Consider adding a browser compatibility check on page load

---

## Verdict

### 🟡 Needs Fixes Before Merge

**Required fixes before merge:**
1. M1 - Add binary message size validation
2. M2 - Add error handling for VAD model loading

**Recommended before production:**
3. M3 - Fix memory growth in long sessions
4. M4 - Add rate limiting

**Can be addressed post-merge:**
- All minor issues (m1-m8)
- Monitoring recommendations

---

## Testing Checklist

Before merging, verify:

- [ ] Basic flow: Start recording → speak → see transcription → stop
- [ ] Language detection works correctly
- [ ] Session persistence: Refresh page, transcript is preserved
- [ ] Reconnection: Disconnect WiFi briefly, reconnects automatically
- [ ] Long session: Run for 10+ minutes, no memory growth
- [ ] Concurrent sessions: Open 4 tabs, 5th should be rejected
- [ ] Error handling: Stop server while recording, see graceful error
- [ ] Mobile: Test on iOS Safari and Android Chrome

---

*Review completed by Jarvis. Questions? Ask in the PR comments.*
