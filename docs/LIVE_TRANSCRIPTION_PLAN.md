# Live Transcription Feature - Implementation Plan

> **Status:** PLANNING - Awaiting Review  
> **Author:** Jarvis  
> **Date:** 2026-02-08  
> **For:** Mirko

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Requirements Analysis](#requirements-analysis)
3. [Model Evaluation & Recommendation](#model-evaluation--recommendation)
4. [Architecture Design](#architecture-design)
5. [Component Breakdown](#component-breakdown)
6. [API Design](#api-design)
7. [Browser Audio Capture](#browser-audio-capture)
8. [Implementation Phases](#implementation-phases)
9. [Open Questions for Mirko](#open-questions-for-mirko)

---

## Executive Summary

This document outlines the plan to add **live transcription** capabilities to Transcribalize. The goal is to enable real-time transcription of video conferences directly from a browser, with user control over audio sources (mic, system audio, or both).

**Key decisions to make:**
- Cloud API (Deepgram/AssemblyAI) vs self-hosted (faster-whisper streaming)
- How to handle system audio capture (browser limitations)
- Output destination (text field vs direct LLM integration)

---

## Requirements Analysis

### Must Have
| Requirement | Details |
|-------------|---------|
| Real-time transcription | Sub-second latency, streaming output |
| Browser-based audio capture | Works in Chrome/Firefox/Edge |
| Multiple audio sources | Mic, system audio, or both |
| Live text output | Continuously updated text window |
| Copy/export capability | Send to clipboard or LLM for analysis |
| No permanent recording | Stream-only, nothing saved |

### Nice to Have
| Feature | Priority |
|---------|----------|
| Speaker diarization | Medium |
| Language auto-detect | Medium |
| Translation (live) | Low |
| Punctuation/formatting | High |
| Word-level timestamps | Low |

### Constraints
- Existing stack: FastAPI backend, Docker, GPU (RTX 4000)
- Current model: faster-whisper large-v3-turbo
- Privacy: No data sent to third parties (unless user opts in)

---

## Model Evaluation & Recommendation

### Options Compared

| Solution | Latency | Accuracy | Cost | Privacy | Streaming Native |
|----------|---------|----------|------|---------|------------------|
| **Deepgram Nova-3** | ~150ms | Excellent | $0.0043/min | ❌ Cloud | ✅ Yes |
| **AssemblyAI Streaming** | ~300ms | Excellent | $0.0050/min | ❌ Cloud | ✅ Yes |
| **ElevenLabs Scribe v2** | ~150ms | Excellent | $0.0040/min | ❌ Cloud | ✅ Yes |
| **faster-whisper (chunked)** | 500ms-2s | Excellent | Self-hosted | ✅ Local | ⚠️ Simulated |
| **whisper.cpp streaming** | ~1s | Good | Self-hosted | ✅ Local | ⚠️ Partial |
| **RealtimeSTT** | ~500ms | Excellent | Self-hosted | ✅ Local | ✅ Yes |
| **Browser Web Speech API** | ~300ms | Medium | Free | ⚠️ Google | ✅ Yes |

### Detailed Analysis

#### 1. Cloud APIs (Deepgram, AssemblyAI, ElevenLabs)

**Pros:**
- True streaming with sub-300ms latency
- Built-in punctuation, diarization, formatting
- No GPU required on backend
- Simple WebSocket integration
- Handles edge cases well (noise, accents)

**Cons:**
- Ongoing cost (~$2.50-3/hour of audio)
- Audio leaves your infrastructure
- Requires internet connection
- API key management

**Best for:** Production deployments where latency is critical and privacy is acceptable.

#### 2. Self-Hosted: faster-whisper with Chunked Streaming

**Pros:**
- Already in your stack
- Full privacy (no external calls)
- No ongoing API costs
- Excellent accuracy (large-v3-turbo)
- GPU accelerated

**Cons:**
- Not true streaming — requires chunking audio (e.g., 2-5 second segments)
- Higher latency (2-5 seconds typical)
- Need to handle chunk boundaries (words may split)
- Requires VAD (Voice Activity Detection) for smart chunking

**Best for:** Privacy-critical use cases where 2-5s latency is acceptable.

#### 3. RealtimeSTT (Python library using faster-whisper)

**Pros:**
- Built-in VAD (WebRTC + Silero)
- Handles real-time audio streams
- Uses faster-whisper under the hood
- Local/private
- Designed for this exact use case

**Cons:**
- Designed for local microphone, not browser WebSocket
- Would need adaptation for web audio streams
- Another dependency to manage
- Not actively maintained (community-driven now)

**Best for:** Prototype/testing, could extract patterns from it.

#### 4. Browser Web Speech API

**Pros:**
- Zero backend needed
- Free
- Low latency
- Built into browsers

**Cons:**
- Inconsistent across browsers
- Sends audio to Google (Chrome)
- No word timestamps
- Can't handle system audio
- Unreliable for long sessions

**Best for:** Fallback/demo only, not production.

---

### 🎯 Recommendation

**Primary: Hybrid Self-Hosted Approach**

Use **faster-whisper with intelligent chunking** for the initial implementation:

1. **VAD-based chunking**: Use WebRTC VAD or Silero to detect speech boundaries
2. **Sliding window**: Process 3-5 second chunks with 1 second overlap
3. **Real-time feedback**: Show interim results, finalize on speech pause
4. **Latency target**: 2-3 seconds (acceptable for meeting notes)

**Rationale:**
- Leverages existing infrastructure (GPU, faster-whisper)
- Full privacy (important for business meetings)
- No ongoing API costs
- Good enough latency for note-taking use case

**Future option:** Add Deepgram/AssemblyAI as optional "low-latency mode" for users who need it and accept cloud processing.

---

## Architecture Design

### System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                            BROWSER                                   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐ │
│  │  Audio Selector │  │  AudioWorklet   │  │  Transcription View  │ │
│  │  (mic/system/   │──│  (capture +     │  │  (live text +        │ │
│  │   both)         │  │   encode PCM)   │  │   copy/analyze)      │ │
│  └─────────────────┘  └────────┬────────┘  └──────────▲───────────┘ │
│                                │                       │             │
│                         WebSocket (binary PCM)    WebSocket (JSON)   │
│                                │                       │             │
└────────────────────────────────┼───────────────────────┼─────────────┘
                                 │                       │
┌────────────────────────────────┼───────────────────────┼─────────────┐
│                         BACKEND (FastAPI)              │             │
│                                │                       │             │
│  ┌─────────────────────────────▼───────────────────────┴───────────┐ │
│  │                    WebSocket Handler                             │ │
│  │   • Receive audio chunks                                        │ │
│  │   • Manage session state                                        │ │
│  │   • Send transcription updates                                  │ │
│  └───────────────────────────────┬─────────────────────────────────┘ │
│                                  │                                   │
│  ┌───────────────────────────────▼─────────────────────────────────┐ │
│  │                    Audio Buffer + VAD                            │ │
│  │   • Ring buffer for incoming audio                              │ │
│  │   • Voice Activity Detection (Silero)                           │ │
│  │   • Chunk on speech boundaries                                  │ │
│  └───────────────────────────────┬─────────────────────────────────┘ │
│                                  │                                   │
│  ┌───────────────────────────────▼─────────────────────────────────┐ │
│  │                    Transcription Queue                           │ │
│  │   • Async queue for GPU processing                              │ │
│  │   • Overlap handling for chunk boundaries                       │ │
│  │   • Result merging + deduplication                              │ │
│  └───────────────────────────────┬─────────────────────────────────┘ │
│                                  │                                   │
│  ┌───────────────────────────────▼─────────────────────────────────┐ │
│  │                    faster-whisper (GPU)                          │ │
│  │   • Process audio chunks                                        │ │
│  │   • Return segments with timestamps                             │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
[Mic/System Audio] 
        │
        ▼
[getUserMedia / getDisplayMedia]
        │
        ▼
[AudioWorklet] ──── 16kHz mono PCM ────▶ [WebSocket]
        │                                      │
        │                                      ▼
        │                              [Backend Buffer]
        │                                      │
        │                              [VAD Detection]
        │                                      │
        │                              [Chunk Segmentation]
        │                                      │
        │                              [Transcription Queue]
        │                                      │
        │                              [faster-whisper GPU]
        │                                      │
        │◀──── JSON {text, is_final} ──────────┘
        │
        ▼
[Transcription Display]
```

---

## Component Breakdown

### Frontend Components

#### 1. AudioSourceSelector
```typescript
// React component for selecting audio input
interface AudioSource {
  type: 'microphone' | 'system' | 'both';
  deviceId?: string;
}

// Uses:
// - navigator.mediaDevices.getUserMedia() for mic
// - navigator.mediaDevices.getDisplayMedia() for system audio
// - Combines both streams for 'both' option
```

#### 2. AudioStreamProcessor
```typescript
// AudioWorklet-based processor
// - Captures raw audio at 16kHz mono
// - Encodes as PCM16 (2 bytes per sample)
// - Sends chunks via WebSocket (~100ms intervals)
```

#### 3. TranscriptionView
```typescript
// Displays live transcription
interface TranscriptionState {
  interim: string;      // Current unfinalized text
  final: string;        // Finalized transcript
  isListening: boolean;
  language: string;
}

// Features:
// - Auto-scroll
// - Copy button
// - "Send to Analyzer" button
// - Clear transcript
```

### Backend Components

#### 1. WebSocket Endpoint (`/ws/transcribe`)
```python
# Handles:
# - Connection lifecycle
# - Binary audio receiving
# - JSON result sending
# - Error handling
```

#### 2. AudioBuffer
```python
# Ring buffer for audio accumulation
# - Configurable size (default 30s)
# - Thread-safe
# - Supports partial reads for overlap
```

#### 3. VADProcessor
```python
# Voice Activity Detection
# - Uses Silero VAD (or WebRTC VAD for speed)
# - Detects speech start/end
# - Triggers chunk creation on silence
```

#### 4. ChunkManager
```python
# Manages audio chunking strategy
# - 3-5 second chunks
# - 1 second overlap for boundary handling
# - Tracks chunk boundaries for deduplication
```

#### 5. TranscriptionWorker
```python
# Async worker for GPU transcription
# - Pulls from queue
# - Runs faster-whisper
# - Merges overlapping results
# - Pushes to WebSocket
```

---

## API Design

### WebSocket Protocol

#### Endpoint
```
ws://localhost:8000/ws/transcribe?language=auto
```

#### Client → Server Messages

**Audio Data (Binary)**
```
[PCM16 audio bytes at 16kHz mono]
```

**Control Messages (JSON)**
```json
// Start session
{"type": "start", "language": "auto"}

// Stop session
{"type": "stop"}

// Ping
{"type": "ping"}
```

#### Server → Client Messages

**Interim Result**
```json
{
  "type": "interim",
  "text": "this is what I'm currently",
  "timestamp": 1707412345.123
}
```

**Final Result**
```json
{
  "type": "final",
  "text": "This is what I'm currently saying.",
  "start": 0.0,
  "end": 2.5,
  "language": "en",
  "confidence": 0.95
}
```

**Status Updates**
```json
{"type": "listening", "status": true}
{"type": "processing", "queue_size": 2}
{"type": "error", "message": "GPU busy, retrying..."}
```

**Pong**
```json
{"type": "pong", "timestamp": 1707412345}
```

### REST Endpoints (Supporting)

```
GET  /live/health           # WebSocket service status
GET  /live/languages        # Available languages
POST /live/session          # Create session (returns session_id)
GET  /live/session/{id}     # Get session transcript history
```

---

## Browser Audio Capture

### Challenge: System Audio

Capturing system audio (what the computer is playing) is restricted in browsers for privacy reasons.

**Solutions:**

#### Option A: Screen Share with Audio (Recommended)
```javascript
// getDisplayMedia with audio: true
const stream = await navigator.mediaDevices.getDisplayMedia({
  video: true,  // Required (even if we don't use it)
  audio: {
    echoCancellation: false,
    noiseSuppression: false,
    autoGainControl: false
  }
});
// Extract audio track
const audioTrack = stream.getAudioTracks()[0];
```

**Pros:** Works in all major browsers, no extensions needed  
**Cons:** User must share a tab/window, video track is required (we can ignore it)

#### Option B: Browser Extension
A simple extension can capture tab audio via `chrome.tabCapture` API.

**Pros:** No screen share UI, cleaner UX  
**Cons:** Requires extension installation, Chrome/Edge only

#### Option C: Virtual Audio Cable (Desktop App)
User routes audio through a virtual device that appears as a mic.

**Pros:** Full system audio, any application  
**Cons:** Requires external software setup

### Recommendation

**Start with Option A** (getDisplayMedia) — works everywhere, good enough for video conference tabs. Document Option B as future enhancement.

### Audio Processing Pipeline

```javascript
// 1. Get audio stream
const stream = await navigator.mediaDevices.getUserMedia({
  audio: { sampleRate: 16000, channelCount: 1 }
});

// 2. Create AudioContext
const ctx = new AudioContext({ sampleRate: 16000 });
const source = ctx.createMediaStreamSource(stream);

// 3. Connect to AudioWorklet
await ctx.audioWorklet.addModule('/static/audio-processor.js');
const processor = new AudioWorkletNode(ctx, 'pcm-processor');
source.connect(processor);

// 4. Handle audio chunks
processor.port.onmessage = (event) => {
  const pcmData = event.data;  // Int16Array
  websocket.send(pcmData.buffer);
};
```

### AudioWorklet Processor

```javascript
// audio-processor.js
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(1600); // 100ms at 16kHz
    this.bufferIndex = 0;
  }

  process(inputs) {
    const input = inputs[0][0]; // Mono
    if (!input) return true;

    for (let i = 0; i < input.length; i++) {
      // Convert float32 [-1, 1] to int16
      const sample = Math.max(-1, Math.min(1, input[i]));
      this.buffer[this.bufferIndex++] = sample * 32767;

      if (this.bufferIndex >= this.buffer.length) {
        this.port.postMessage(this.buffer.slice());
        this.bufferIndex = 0;
      }
    }
    return true;
  }
}

registerProcessor('pcm-processor', PCMProcessor);
```

---

## Implementation Phases

### Phase 1: Core Infrastructure (Week 1)
**Goal:** WebSocket audio streaming end-to-end

- [ ] WebSocket endpoint in FastAPI
- [ ] Basic audio buffer accumulation
- [ ] Simple chunked transcription (fixed 3s chunks)
- [ ] Frontend: Mic capture + AudioWorklet
- [ ] Frontend: Basic transcription display
- [ ] Integration test with real mic

**Deliverable:** Working demo with mic → text (3-5s latency)

### Phase 2: Smart Chunking (Week 2)
**Goal:** Reduce latency and improve quality

- [ ] Integrate Silero VAD
- [ ] Speech boundary detection
- [ ] Overlap handling and deduplication
- [ ] Interim results (show text before finalized)
- [ ] Language auto-detection

**Deliverable:** Responsive transcription with ~2s latency

### Phase 3: System Audio (Week 3)
**Goal:** Capture video conference audio

- [ ] getDisplayMedia integration
- [ ] Audio source selector UI
- [ ] Mixed audio (mic + system) option
- [ ] Audio level indicators
- [ ] Connection status UI

**Deliverable:** Full audio capture options

### Phase 4: Polish & Integration (Week 4)
**Goal:** Production-ready feature

- [ ] Copy transcript button
- [ ] "Send to Analyzer" integration
- [ ] Session persistence (refresh recovery)
- [ ] Error handling and reconnection
- [ ] Mobile browser testing
- [ ] Performance optimization
- [ ] Documentation

**Deliverable:** Shippable feature

### Phase 5: Optional Enhancements (Future)
- [ ] Deepgram/AssemblyAI as optional low-latency backend
- [ ] Speaker diarization
- [ ] Browser extension for cleaner tab capture
- [ ] Live translation
- [ ] Keyboard shortcuts

---

## Open Questions for Mirko

### 1. **Privacy vs Latency Trade-off**
> Are you okay with 2-3 second latency (self-hosted) or do you need sub-second (cloud API)?
> 
> Cloud APIs (Deepgram/AssemblyAI) are $2.50-3/hour but give ~200ms latency.

### 2. **System Audio Priority**
> How important is capturing system audio (what the computer plays)?
> 
> - **Critical:** We need to invest in getDisplayMedia + possibly browser extension
> - **Nice to have:** We can start with mic-only and add later

### 3. **Output Integration**
> Where should the transcript go after the meeting?
> 
> - **Just copy/paste:** Simple text field with copy button
> - **Direct to Analyzer:** Auto-send to LLM for summary/analysis
> - **Save to file:** Download as markdown/txt
> - **All of the above?**

### 4. **Multi-Language Support**
> Do you need transcription in multiple languages?
> 
> - Auto-detect is supported but slows down first chunk
> - Fixed language (en/de) is faster and more accurate

### 5. **Concurrent Sessions**
> Will multiple users transcribe simultaneously?
> 
> Current GPU can handle ~1-2 concurrent real-time streams. More would require queuing or additional GPU resources.

### 6. **Deployment Target**
> Where will this run?
> 
> - **Local dev machine:** Simple docker-compose
> - **Home server with GPU:** Same as current transcriber
> - **Cloud (Modal/RunPod):** Need to consider latency to WebSocket
> - **Browser-only (Web Speech API fallback):** Very limited quality

### 7. **Analytics / History**
> Should we save any session metadata?
> 
> - Meeting duration, language, word count, etc.
> - Or truly ephemeral (nothing persisted)?

---

## Technical Notes

### Faster-Whisper Streaming Limitation

Faster-whisper is not natively streaming — it processes complete audio files. Our "streaming" is simulated:

1. Accumulate audio in buffer
2. When VAD detects speech pause (or 5s max), extract chunk
3. Transcribe chunk
4. Merge with previous results

This adds latency but maintains accuracy.

### Memory Considerations

| Component | Memory |
|-----------|--------|
| faster-whisper model | ~3GB VRAM |
| Audio buffer (30s) | ~1MB RAM |
| WebSocket connections | ~1MB each |
| VAD model (Silero) | ~50MB RAM |

Should fit comfortably on RTX 4000 (20GB VRAM).

### Fallback Strategy

If GPU is unavailable or overloaded:
1. Queue with estimated wait time
2. Fall back to CPU (slower but works)
3. Optionally: Use Web Speech API as emergency fallback

---

## References

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Silero VAD](https://github.com/snakers4/silero-vad)
- [Deepgram Streaming Docs](https://developers.deepgram.com/docs/getting-started-with-live-streaming-audio)
- [AssemblyAI Streaming](https://www.assemblyai.com/docs/speech-to-text/streaming)
- [MDN: getDisplayMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)
- [AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet)
- [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) (patterns to learn from)

---

*This plan is ready for review. No implementation until approved.*
