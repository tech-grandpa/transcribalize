# Live transcription design and implementation

Live transcription is implemented in the Transcribalize browser application and FastAPI service. This document describes the current data path, WebSocket protocol, operational limits, and known constraints.

For user setup, start with the [project README](../README.md). For endpoint-level details, see the [API reference](../transcriber/docs/API.md).

## User workflow

The Live mode in the main browser application can capture:

- microphone audio through `navigator.mediaDevices.getUserMedia()`
- browser-tab audio through `navigator.mediaDevices.getDisplayMedia()`
- microphone and tab audio mixed in the browser

The browser converts the selected source to 16 kHz mono PCM16 and sends binary chunks to `/ws/transcribe`. The same page displays final transcript chunks, tracks the detected language, retains recovery data in browser local storage, and can export the transcript as Markdown.

Chrome and Firefox provide the best tab-audio capture support. Safari can capture microphone input but has limited tab-audio support. Browser security rules require user interaction and permission before capture starts.

## Server data path

```text
Browser capture
      |
      v
Web Audio processing
16 kHz mono PCM16
      |
      v
WS /ws/transcribe
      |
      v
size and connection limits
      |
      v
Silero VAD smart chunker
      |
      v
faster-whisper large-v3-turbo
      |
      v
final transcript messages
```

Live transcription always uses the shared Whisper model. File-only ASR choices such as Parakeet and Granite do not affect live sessions.

The server's `SmartChunker` waits for speech boundaries where possible. Its current defaults are:

- 16 kHz sample rate
- 1 second minimum chunk duration
- 10 second maximum chunk duration
- `0.5` VAD threshold

Whisper runs with language auto-detection or the selected `en`/`de` language. After auto-detection succeeds, the session reuses the detected language for later chunks. A small recent-text window suppresses duplicate chunk results.

## WebSocket protocol

Connect to:

```text
ws://HOST/ws/transcribe?language=auto
```

Use `wss://` when the application is behind TLS.

### Client messages

Binary messages contain 16 kHz mono PCM16 audio. Each message must be no larger than 32 KiB.

Text control messages use JSON:

```json
{"type":"ping"}
```

```json
{"type":"language","language":"de"}
```

```json
{"type":"stop"}
```

### Server messages

A successful connection starts with:

```json
{"type":"ready","sample_rate":16000,"vad_enabled":true}
```

Transcript chunks use the `final` type:

```json
{
  "type": "final",
  "text": "Example transcript text.",
  "language": "en",
  "timestamp": 0,
  "audio_offset": 4.2
}
```

The server can also return `pong`, `language_set`, `stopped`, or `error` messages. Clients must handle an error followed by connection closure when capacity or rate limits are reached.

## Capacity and abuse limits

The default live-session limit is four concurrent WebSockets. Set `MAX_CONCURRENT_SESSIONS` to change it.

The service also enforces:

- at most 10 connection attempts per client address per 60 seconds
- a 32 KiB maximum binary audio message
- a bounded active-session set

These checks protect the application process, but they are not a substitute for reverse-proxy authentication, request throttling, connection limits, and network isolation.

## Privacy and storage

Live audio is processed by the self-hosted service and is not sent to the configured LLM provider. The server keeps audio in memory long enough to form transcription chunks; it does not intentionally save a live recording.

The browser stores transcript recovery text and related session metadata in local storage. Users can clear the live transcript from the UI or clear site data in the browser.

## Browser security

UI responses include a Content Security Policy that allows connections only to the same host over HTTP/WebSocket or HTTPS/Secure WebSocket. The service validates the HTTP `Host` authority before including it in that policy.

Direct bracketed-IPv6 host authorities are rejected because browser handling of IPv6 WebSocket sources in Content Security Policy is inconsistent. Put IPv6 deployments behind a DNS hostname at the reverse proxy.

## Current limitations

- Live transcription produces chunk-level text, not word-level timestamps.
- Speaker diarization is not implemented.
- Live translation is not implemented.
- Live sessions use Whisper only.
- Tab-audio availability depends on the browser and what the selected surface permits sharing.
- The application has no built-in authentication or per-user isolation.

## Implementation map

- [`transcriber/static/index.html`](../transcriber/static/index.html): capture controls, Web Audio graph, WebSocket client, recovery, copy, and export
- [`transcriber/static/audio-processor.js`](../transcriber/static/audio-processor.js): browser audio processing
- [`transcriber/app/live_transcription.py`](../transcriber/app/live_transcription.py): protocol, limits, VAD chunk queue, inference, and session cleanup
- [`transcriber/app/vad.py`](../transcriber/app/vad.py): Silero VAD smart chunking
- [`transcriber/app/main.py`](../transcriber/app/main.py): WebSocket route, language discovery, and UI security policy
- [`transcriber/tests/test_api.py`](../transcriber/tests/test_api.py): API, security-header, and browser-asset regression coverage
