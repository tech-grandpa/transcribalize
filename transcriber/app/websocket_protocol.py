"""Bound SansIO message assembly until Uvicorn provides a fragment limit."""

from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol
from websockets.frames import Frame


MAX_MESSAGE_FRAGMENTS = 4096


class BoundedWebSocketsProtocol(WebSocketsSansIOProtocol):
    """Keep Uvicorn's SansIO behavior, but bound its per-message frame list."""

    frames: list[bytes]

    def handle_text(self, event: Frame) -> None:
        if not self.close_sent:
            super().handle_text(event)

    def handle_bytes(self, event: Frame) -> None:
        if not self.close_sent:
            super().handle_bytes(event)

    def handle_parser_exception(self) -> None:
        # Parsing can fail before Uvicorn dispatches a continuation from the
        # same TCP read, so release any previously retained message fragments.
        self.frames.clear()
        super().handle_parser_exception()

    def handle_cont(self, event: Frame) -> None:
        # Uvicorn may already have parsed more frames from the same TCP read.
        if self.close_sent:
            return
        if len(self.frames) >= MAX_MESSAGE_FRAGMENTS:
            self.frames.clear()
            self.stop_keepalive()
            self.conn.fail(1009, "too many fragments")
            self.handle_parser_exception()
            return
        super().handle_cont(event)
