"""Wire-level tests of the WebSocket implementation selected by the image."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import importlib
import socket
import sys
import types

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from websockets.frames import Frame, Opcode

from test_api import main_module  # noqa: F401 (shared pytest fixture)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def image_ws_protocol():
    command = next(
        line[4:]
        for line in (ROOT / "Dockerfile").read_text().splitlines()
        if line.startswith("CMD ")
    )
    args = json.loads(command)
    return args[args.index("--ws") + 1] if "--ws" in args else "auto"


async def echo_app(scope, receive, send):
    assert (await receive())["type"] == "websocket.connect"
    await send({"type": "websocket.accept"})
    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            return
        await send(
            {
                "type": "websocket.send",
                **{key: message[key] for key in ("text", "bytes") if key in message},
            }
        )


@asynccontextmanager
async def running_server(app=echo_app):
    config = uvicorn.Config(
        app, ws=image_ws_protocol(), lifespan="off", log_level="error"
    )
    config.load()
    protocols = []

    class ObservedProtocol(config.ws_protocol_class):
        def connection_made(self, transport):
            protocols.append(self)
            super().connection_made(transport)

    config.ws_protocol_class = ObservedProtocol
    server = uvicorn.Server(config)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if task.done():
                        await task
                        raise AssertionError("Server exited before startup")
                    await asyncio.sleep(0.01)
            yield f"ws://127.0.0.1:{sock.getsockname()[1]}", protocols
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 5)


def send_frames(client, frames):
    # Raw masked RFC 6455 frames let us leave a fragmented message unfinished.
    client.transport.write(b"".join(frame.serialize(mask=True) for frame in frames))


@pytest.mark.parametrize("opcode", [Opcode.BINARY, Opcode.TEXT])
@pytest.mark.parametrize("final", [False, True])
def test_image_rejects_more_than_4096_fragments(opcode, final):
    async def scenario():
        async with running_server() as (url, protocols):
            async with connect(url, compression=None) as client:
                send_frames(
                    client,
                    [Frame(opcode, b"", fin=False)]
                    + [Frame(Opcode.CONT, b"", fin=False) for _ in range(4095)],
                )
                # A control frame flushes the prefix without ending the message.
                await asyncio.wait_for(await client.ping(), 2)
                assert len(protocols[0].frames) == 4096
                send_frames(client, [Frame(Opcode.CONT, b"", fin=final)])
                with pytest.raises(ConnectionClosed) as closed:
                    await asyncio.wait_for(client.recv(), 2)
                assert closed.value.rcvd.code == 1009
                assert protocols[0].frames == []
                assert protocols[0].close_sent

    asyncio.run(scenario())


@pytest.mark.parametrize("next_opcode", [Opcode.CONT, Opcode.BINARY, Opcode.TEXT])
def test_overflow_discards_remaining_frames_in_same_tcp_batch(next_opcode):
    async def scenario():
        async with running_server() as (url, protocols):
            async with connect(url, compression=None) as client:
                frames = [Frame(Opcode.BINARY, b"", fin=False)] + [
                    Frame(Opcode.CONT, b"", fin=False) for _ in range(4096)
                ]
                if next_opcode != Opcode.CONT:
                    frames.append(Frame(Opcode.CONT, b"", fin=True))
                frames.append(Frame(next_opcode, b"discard me", fin=False))
                send_frames(client, frames)
                with pytest.raises(ConnectionClosed) as closed:
                    await asyncio.wait_for(client.recv(), 2)
                assert closed.value.rcvd.code == 1009
                assert protocols[0].frames == []
                assert not any(
                    event["type"] == "websocket.receive"
                    for event in protocols[0].queue._queue
                )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "opcode,payload", [(Opcode.BINARY, b"ok"), (Opcode.TEXT, "ä".encode())]
)
def test_4096_fragments_allow_control_frames_and_reset_per_message(opcode, payload):
    async def scenario():
        async with running_server() as (url, protocols):
            async with connect(url, compression=None) as client:
                for _ in range(2):
                    send_frames(
                        client,
                        [Frame(opcode, payload[:1], fin=False)]
                        + [Frame(Opcode.CONT, b"", fin=False) for _ in range(4094)],
                    )
                    await asyncio.wait_for(await client.ping(), 2)
                    send_frames(client, [Frame(Opcode.CONT, payload[1:], fin=True)])
                    expected = payload if opcode == Opcode.BINARY else payload.decode()
                    assert await asyncio.wait_for(client.recv(), 2) == expected
                    assert protocols[0].frames == []

    asyncio.run(scenario())


def test_compressed_fragmented_messages():
    async def scenario():
        async with running_server() as (url, _):
            async with connect(url) as client:
                assert client.protocol.extensions
                await client.send([b"audio", b"", b"data"])
                assert await asyncio.wait_for(client.recv(), 2) == b"audiodata"
                # send() adds one final continuation frame to its iterable.
                await client.send([b""] * 4097)
                with pytest.raises(ConnectionClosed) as closed:
                    await asyncio.wait_for(client.recv(), 2)
                assert closed.value.rcvd.code == 1009

    asyncio.run(scenario())


@pytest.fixture()
def live_app(main_module, monkeypatch):  # noqa: F811 (shared pytest fixture injection)
    # Reuse the API suite's isolated import setup, but load the REAL live handler.
    main = main_module
    vad = types.ModuleType("app.vad")

    class SyntheticChunker:
        def __init__(self, **kwargs):
            pass

        def add_audio(self, audio):
            return [audio] if len(audio) else []

        def flush(self):
            return None

    vad.SmartChunker = SyntheticChunker
    monkeypatch.setitem(sys.modules, "app.vad", vad)
    monkeypatch.delitem(sys.modules, "app.live_transcription")
    live = importlib.import_module("app.live_transcription")
    monkeypatch.setattr(
        main, "handle_transcription_session", live.handle_transcription_session
    )
    monkeypatch.setattr(live, "get_model", lambda: object())

    async def synthetic_transcribe(**kwargs):
        return {
            "text": "Synthetic speech",
            "language": "en",
            "language_probability": 1.0,
        }

    monkeypatch.setattr(live, "transcribe_chunk", synthetic_transcribe)
    return main.app, live


async def receive_json(client):
    return json.loads(await asyncio.wait_for(client.recv(), 2))


async def assert_sessions_clean(live):
    async with asyncio.timeout(2):
        while live._active_sessions:
            await asyncio.sleep(0.01)


def test_live_normal_error_and_stop_paths(live_app):
    app, live = live_app

    async def scenario():
        async with running_server(app) as (url, _):
            async with connect(url + "/ws/transcribe") as client:
                assert (await receive_json(client))["type"] == "ready"
                await client.send('{"type":"ping"}')
                assert (await receive_json(client))["type"] == "pong"
                await client.send('{"type":"language","language":"de"}')
                assert await receive_json(client) == {
                    "type": "language_set",
                    "language": "de",
                }
                await client.send("not json")
                assert await receive_json(client) == {
                    "type": "error",
                    "message": "Invalid JSON",
                }
                await client.send(b"\x00" * (live.MAX_AUDIO_CHUNK_SIZE + 1))
                assert (
                    "Audio chunk too large" in (await receive_json(client))["message"]
                )
                await client.send([b"\x00\x00", b"\x01\x00"])
                result = await receive_json(client)
                assert result["type"] == "final"
                assert result["text"] == "Synthetic speech"
                await client.send('{"type":"stop"}')
                assert await receive_json(client) == {"type": "stopped"}
                # Existing behavior, not changed by transport hardening.
                with pytest.raises(ConnectionClosed) as closed:
                    await asyncio.wait_for(client.recv(), 2)
                assert closed.value.rcvd is None  # client observes 1006
            await assert_sessions_clean(live)

    asyncio.run(scenario())


@pytest.mark.parametrize("in_flight", [False, True])
def test_live_disconnect_cleans_up(live_app, monkeypatch, in_flight):
    app, live = live_app

    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending_transcribe(**kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        monkeypatch.setattr(live, "transcribe_chunk", pending_transcribe)
        async with running_server(app) as (url, _):
            async with connect(url + "/ws/transcribe") as client:
                assert (await receive_json(client))["type"] == "ready"
                if in_flight:
                    await client.send(b"\x00\x00")
                    await asyncio.wait_for(started.wait(), 2)
            await assert_sessions_clean(live)
            if in_flight:
                assert cancelled.is_set()

    asyncio.run(scenario())


def test_live_fragment_overflow_cleans_up(live_app):
    app, live = live_app

    async def scenario():
        async with running_server(app) as (url, protocols):
            async with connect(url + "/ws/transcribe", compression=None) as client:
                assert (await receive_json(client))["type"] == "ready"
                send_frames(
                    client,
                    [Frame(Opcode.BINARY, b"", fin=False)]
                    + [Frame(Opcode.CONT, b"", fin=False) for _ in range(4096)],
                )
                with pytest.raises(ConnectionClosed) as closed:
                    await asyncio.wait_for(client.recv(), 2)
                assert closed.value.rcvd.code == 1009
                assert protocols[0].frames == []
            await assert_sessions_clean(live)

    asyncio.run(scenario())
