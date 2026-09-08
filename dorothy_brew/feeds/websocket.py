"""A minimal RFC 6455 WebSocket client.

Just enough of the protocol to hold a public market-data stream open: the
opening handshake, masked client frames, fragmentation, and the ping/pong and
close control frames. No third-party dependency, which is the point — the rest
of this package runs on the standard library too.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import time
import urllib.parse
from typing import Dict, List, Optional, Tuple, Union

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
CONTROL_OPS = {OP_CLOSE, OP_PING, OP_PONG}

DEFAULT_MAX_MESSAGE = 8 << 20      # 8 MiB is far beyond any market-data frame


class WebSocketError(RuntimeError):
    pass


class WebSocketClosed(WebSocketError):
    def __init__(self, code: int = 1006, reason: str = ""):
        super().__init__(f"websocket closed ({code}) {reason}".strip())
        self.code = code
        self.reason = reason


class Frame:
    __slots__ = ("fin", "opcode", "payload")

    def __init__(self, fin: bool, opcode: int, payload: bytes):
        self.fin = fin
        self.opcode = opcode
        self.payload = payload


def parse_url(url: str) -> Tuple[str, int, str, bool]:
    """ws(s)://host[:port]/path -> (host, port, path, secure)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("ws", "wss"):
        raise ValueError(f"not a websocket url: {url}")
    secure = parts.scheme == "wss"
    port = parts.port or (443 if secure else 80)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    if not parts.hostname:
        raise ValueError(f"no host in url: {url}")
    return parts.hostname, port, path, secure


def accept_token(key: str) -> str:
    """The Sec-WebSocket-Accept value a server must echo back."""
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes, mask: bool = True, fin: bool = True) -> bytes:
    """Serialise one frame. Client frames are always masked, as the RFC demands."""
    head = bytearray()
    head.append((0x80 if fin else 0x00) | (opcode & 0x0F))
    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        head.append(mask_bit | length)
    elif length < (1 << 16):
        head.append(mask_bit | 126)
        head += struct.pack("!H", length)
    else:
        head.append(mask_bit | 127)
        head += struct.pack("!Q", length)
    if not mask:
        return bytes(head) + payload
    key = os.urandom(4)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(head) + key + masked


def decode_frame(buffer: bytes) -> Tuple[Optional[Frame], int]:
    """Parse one frame from ``buffer``. Returns (frame or None, bytes consumed)."""
    if len(buffer) < 2:
        return None, 0
    b0, b1 = buffer[0], buffer[1]
    if b0 & 0x70:
        raise WebSocketError("reserved bits set (no extension was negotiated)")
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    offset = 2
    if length == 126:
        if len(buffer) < offset + 2:
            return None, 0
        length = struct.unpack("!H", buffer[offset:offset + 2])[0]
        offset += 2
    elif length == 127:
        if len(buffer) < offset + 8:
            return None, 0
        length = struct.unpack("!Q", buffer[offset:offset + 8])[0]
        offset += 8
    if opcode in CONTROL_OPS and (length > 125 or not fin):
        raise WebSocketError("control frames must be short and unfragmented")
    key = b""
    if masked:
        if len(buffer) < offset + 4:
            return None, 0
        key = buffer[offset:offset + 4]
        offset += 4
    if len(buffer) < offset + length:
        return None, 0
    payload = bytes(buffer[offset:offset + length])
    if masked:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return Frame(fin, opcode, payload), offset + length


class WebSocket:
    """A blocking client socket. ``recv`` returns None when nothing arrived in time."""

    def __init__(self, sock, max_message_bytes: int = DEFAULT_MAX_MESSAGE):
        self.sock = sock
        self.max_message_bytes = max_message_bytes
        self.closed = False
        self.close_code: Optional[int] = None
        self._buf = bytearray()
        self._fragments: List[bytes] = []
        self._fragment_op: Optional[int] = None

    # -- construction -------------------------------------------------------
    @classmethod
    def connect(cls, url: str, timeout: float = 10.0,
                headers: Optional[Dict[str, str]] = None,
                ssl_context: Optional[ssl.SSLContext] = None,
                sock: Optional[object] = None,
                max_message_bytes: int = DEFAULT_MAX_MESSAGE) -> "WebSocket":
        """Open a connection and perform the upgrade handshake.

        Pass ``sock`` to drive an already-connected socket (used by the tests).
        """
        host, port, path, secure = parse_url(url)
        if sock is None:
            raw = socket.create_connection((host, port), timeout=timeout)
            if secure:
                context = ssl_context or ssl.create_default_context()
                raw = context.wrap_socket(raw, server_hostname=host)
            sock = raw
        ws = cls(sock, max_message_bytes)
        try:
            ws._handshake(host, port, path, secure, headers or {}, timeout)
        except Exception:
            ws._hard_close()
            raise
        return ws

    def _handshake(self, host: str, port: int, path: str, secure: bool,
                   extra: Dict[str, str], timeout: float) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        default_port = 443 if secure else 80
        host_header = host if port == default_port else f"{host}:{port}"
        lines = [f"GET {path} HTTP/1.1",
                 f"Host: {host_header}",
                 "Upgrade: websocket",
                 "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}",
                 "Sec-WebSocket-Version: 13"]
        lines += [f"{k}: {v}" for k, v in extra.items()]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))

        deadline = time.monotonic() + timeout
        while b"\r\n\r\n" not in self._buf:
            if time.monotonic() > deadline:
                raise WebSocketError("timed out waiting for the handshake response")
            self._fill(max(0.1, deadline - time.monotonic()))
        head, _, rest = bytes(self._buf).partition(b"\r\n\r\n")
        self._buf = bytearray(rest)
        text = head.decode("latin-1")
        status = text.split("\r\n", 1)[0]
        if " 101" not in status:
            raise WebSocketError(f"handshake rejected: {status}")
        received = ""
        for line in text.split("\r\n")[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "sec-websocket-accept":
                received = value.strip()
        if received != accept_token(key):
            raise WebSocketError("handshake accept token did not match the key")

    # -- io -----------------------------------------------------------------
    def _fill(self, timeout: float) -> None:
        self.sock.settimeout(max(0.0, timeout))
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            raise
        except ssl.SSLWantReadError:
            return
        if not chunk:
            self._hard_close()                    # release the fd, not just the flag
            raise WebSocketClosed(1006, "peer closed the connection")
        self._buf += chunk

    def _next_frame(self, timeout: float) -> Optional[Frame]:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            frame, consumed = decode_frame(self._buf)
            if frame is not None:
                del self._buf[:consumed]
                return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                self._fill(remaining)
            except socket.timeout:
                return None

    def send(self, data: Union[str, bytes]) -> None:
        if self.closed:
            raise WebSocketClosed(self.close_code or 1006, "cannot send on a closed socket")
        if isinstance(data, str):
            self._send_frame(OP_TEXT, data.encode("utf-8"))
        else:
            self._send_frame(OP_BINARY, data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        self.sock.sendall(encode_frame(opcode, payload, mask=True))

    def ping(self, payload: bytes = b"") -> None:
        self._send_frame(OP_PING, payload)

    def recv(self, timeout: float = 30.0) -> Optional[str]:
        """Next application message, or None if none arrived within ``timeout``.

        Control frames are handled here: a ping is answered with a pong, and a
        close frame raises :class:`WebSocketClosed`.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self.closed:
                raise WebSocketClosed(self.close_code or 1006, "socket already closed")
            frame = self._next_frame(max(0.0, deadline - time.monotonic()))
            if frame is None:
                return None
            if frame.opcode == OP_PING:
                self._send_frame(OP_PONG, frame.payload)
                continue
            if frame.opcode == OP_PONG:
                continue
            if frame.opcode == OP_CLOSE:
                code, reason = 1005, ""
                if len(frame.payload) >= 2:
                    code = struct.unpack("!H", frame.payload[:2])[0]
                    reason = frame.payload[2:].decode("utf-8", "replace")
                self.close_code = code
                self._respond_close(frame.payload[:2])
                raise WebSocketClosed(code, reason)

            if frame.opcode == OP_CONTINUATION:
                if self._fragment_op is None:
                    raise WebSocketError("continuation frame without a start frame")
            else:
                if self._fragment_op is not None:
                    raise WebSocketError("new data frame while a message was fragmented")
                self._fragment_op = frame.opcode
            self._fragments.append(frame.payload)
            if sum(len(f) for f in self._fragments) > self.max_message_bytes:
                self.close(1009, "message too big")
                raise WebSocketError("message exceeded max_message_bytes")
            if not frame.fin:
                continue

            payload = b"".join(self._fragments)
            opcode = self._fragment_op
            self._fragments, self._fragment_op = [], None
            if opcode == OP_TEXT:
                return payload.decode("utf-8", "replace")
            return payload.decode("latin-1")          # binary: rare here, keep it lossless

    # -- teardown -----------------------------------------------------------
    def _respond_close(self, echo: bytes) -> None:
        try:
            self._send_frame(OP_CLOSE, echo)
        except OSError:
            pass
        self._hard_close()

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", code) + reason.encode("utf-8"))
        except (OSError, WebSocketError):
            pass
        self.close_code = code
        self._hard_close()

    def _hard_close(self) -> None:
        self.closed = True
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
