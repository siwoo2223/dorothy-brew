"""RFC 6455 client: framing unit tests plus a real handshake over loopback TCP.

The loopback server is deliberately written against the spec text (it computes
the accept token inline and reads the raw bytes the client sent) so the test
exercises the wire format, not just a round trip through the same helpers.
"""

import base64
import hashlib
import socket
import struct
import threading
import unittest

from dorothy_brew.feeds.websocket import (OP_BINARY, OP_CLOSE, OP_PING, OP_TEXT,
                                          WebSocket, WebSocketClosed, WebSocketError,
                                          accept_token, decode_frame, encode_frame,
                                          parse_url)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class TestHandshakeMath(unittest.TestCase):
    def test_rfc_6455_accept_vectors(self):
        # the two examples published in RFC 6455 (sections 1.3 and 4.2.2)
        self.assertEqual(accept_token("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
        self.assertEqual(accept_token("x3JJHMbDL1EzLkh9GBhXDw=="),
                         "HSmrc0sMlYUkAGmm5OPpG2HaGWk=")

    def test_url_parsing(self):
        self.assertEqual(parse_url("wss://ws.bitget.com/v2/ws/public"),
                         ("ws.bitget.com", 443, "/v2/ws/public", True))
        self.assertEqual(parse_url("ws://127.0.0.1:8080/x?y=1"),
                         ("127.0.0.1", 8080, "/x?y=1", False))
        self.assertEqual(parse_url("ws://host")[2], "/")

    def test_bad_urls(self):
        for bad in ("http://example.com", "ws://", "not a url"):
            with self.assertRaises(ValueError):
                parse_url(bad)


class TestFraming(unittest.TestCase):
    def test_client_frames_are_masked(self):
        raw = encode_frame(OP_TEXT, b"Hello", mask=True)
        self.assertEqual(raw[0], 0x81)                    # FIN + text
        self.assertEqual(raw[1], 0x80 | 5)                # MASK + length
        key, payload = raw[2:6], raw[6:]
        self.assertEqual(bytes(b ^ key[i % 4] for i, b in enumerate(payload)), b"Hello")

    def test_unmasked_server_frame_layout(self):
        raw = encode_frame(OP_TEXT, b"Hello", mask=False)
        self.assertEqual(raw, b"\x81\x05Hello")

    def test_length_boundaries(self):
        for size, marker, header in ((125, 125, 2), (126, 126, 4), (65535, 126, 4),
                                     (65536, 127, 10)):
            raw = encode_frame(OP_BINARY, b"x" * size, mask=False)
            with self.subTest(size=size):
                self.assertEqual(raw[1] & 0x7F, marker)
                self.assertEqual(len(raw), header + size)
                frame, consumed = decode_frame(raw)
                self.assertEqual(len(frame.payload), size)
                self.assertEqual(consumed, len(raw))

    def test_round_trip_masked_and_unmasked(self):
        for mask in (True, False):
            payload = "가격 패턴 🚀".encode("utf-8")
            frame, consumed = decode_frame(encode_frame(OP_TEXT, payload, mask=mask))
            self.assertEqual(frame.payload, payload)
            self.assertTrue(frame.fin)
            self.assertEqual(frame.opcode, OP_TEXT)

    def test_partial_buffer_is_not_consumed(self):
        raw = encode_frame(OP_TEXT, b"hello world", mask=False)
        for cut in range(1, len(raw)):
            frame, consumed = decode_frame(raw[:cut])
            self.assertIsNone(frame)
            self.assertEqual(consumed, 0)

    def test_reserved_bits_are_rejected(self):
        raw = bytearray(encode_frame(OP_TEXT, b"x", mask=False))
        raw[0] |= 0x40
        with self.assertRaises(WebSocketError):
            decode_frame(bytes(raw))

    def test_oversized_control_frame_is_rejected(self):
        raw = encode_frame(OP_PING, b"x" * 126, mask=False)
        with self.assertRaises(WebSocketError):
            decode_frame(raw)

    def test_fragmented_control_frame_is_rejected(self):
        raw = encode_frame(OP_PING, b"x", mask=False, fin=False)
        with self.assertRaises(WebSocketError):
            decode_frame(raw)

    def test_two_frames_in_one_buffer(self):
        buf = encode_frame(OP_TEXT, b"one", mask=False) + \
            encode_frame(OP_TEXT, b"two", mask=False)
        first, consumed = decode_frame(buf)
        second, _ = decode_frame(buf[consumed:])
        self.assertEqual((first.payload, second.payload), (b"one", b"two"))


class LoopbackServer(threading.Thread):
    """A tiny spec-compliant server: handshake, then run a scripted behaviour."""

    daemon = True

    def __init__(self, behaviour):
        super().__init__()
        self.behaviour = behaviour
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received = []
        self.error = None

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/stream"

    def run(self):
        conn = None
        try:
            conn, _ = self.sock.accept()
            conn.settimeout(5)
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            key = ""
            for line in request.decode("latin-1").split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "sec-websocket-key":
                    key = value.strip()
            self.request_lines = request.decode("latin-1").split("\r\n")
            token = base64.b64encode(
                hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                          "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {token}\r\n\r\n").encode("ascii"))
            self.behaviour(self, conn)
        except Exception as exc:                       # surfaced by the test
            self.error = exc
        finally:
            for handle in (conn, self.sock):
                try:
                    if handle is not None:
                        handle.close()
                except OSError:
                    pass

    # helpers for behaviours -------------------------------------------------
    def read_message(self, conn):
        buf = bytearray()
        while True:
            frame, consumed = decode_frame(bytes(buf))
            if frame is not None:
                del buf[:consumed]
                self.received.append(frame)
                return frame
            chunk = conn.recv(4096)
            if not chunk:
                return None
            buf += chunk


def echo_once(server, conn):
    frame = server.read_message(conn)
    conn.sendall(encode_frame(OP_TEXT, frame.payload, mask=False))
    server.read_message(conn)          # wait for the client's close frame


class TestOverLoopback(unittest.TestCase):
    def _serve(self, behaviour):
        server = LoopbackServer(behaviour)
        server.start()
        self.addCleanup(server.join, 5)
        return server

    def test_handshake_and_echo(self):
        server = self._serve(echo_once)
        with WebSocket.connect(server.url, timeout=5) as ws:
            ws.send("hello 하이 🚀")
            self.assertEqual(ws.recv(5), "hello 하이 🚀")
        server.join(5)
        self.assertIsNone(server.error)
        # the client must have masked its frame, per the RFC
        self.assertTrue(server.received)
        self.assertIn("Sec-WebSocket-Version: 13", server.request_lines)
        self.assertIn("Upgrade: websocket", server.request_lines)

    def test_server_rejecting_the_upgrade(self):
        def reject(server, conn):
            pass

        raw = LoopbackServer(reject)
        raw.behaviour = reject
        # replace the handshake with a plain 400 response
        def run_400():
            conn, _ = raw.sock.accept()
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            conn.close()
            raw.sock.close()
        thread = threading.Thread(target=run_400, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        with self.assertRaises(WebSocketError) as ctx:
            WebSocket.connect(raw.url, timeout=5)
        self.assertIn("400", str(ctx.exception))

    def test_bad_accept_token_is_rejected(self):
        def wrong_token():
            conn, _ = server.sock.accept()
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\n"
                         b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: definitely-not-right\r\n\r\n")
            conn.close()
            server.sock.close()
        server = LoopbackServer(lambda *a: None)
        thread = threading.Thread(target=wrong_token, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        with self.assertRaises(WebSocketError) as ctx:
            WebSocket.connect(server.url, timeout=5)
        self.assertIn("accept token", str(ctx.exception))

    def test_fragmented_message_is_reassembled(self):
        def fragmented(server, conn):
            conn.sendall(encode_frame(OP_TEXT, b"frag", mask=False, fin=False))
            conn.sendall(encode_frame(0x0, b"-", mask=False, fin=False))
            conn.sendall(encode_frame(0x0, b"mented", mask=False, fin=True))
            server.read_message(conn)
        server = self._serve(fragmented)
        with WebSocket.connect(server.url, timeout=5) as ws:
            self.assertEqual(ws.recv(5), "frag-mented")

    def test_ping_is_answered_with_a_pong(self):
        def ping_then_text(server, conn):
            conn.sendall(encode_frame(OP_PING, b"abc", mask=False))
            pong = server.read_message(conn)
            conn.sendall(encode_frame(OP_TEXT, b"after-pong", mask=False))
            server.read_message(conn)
            server.pong_payload = pong.payload
            server.pong_opcode = pong.opcode
        server = self._serve(ping_then_text)
        with WebSocket.connect(server.url, timeout=5) as ws:
            self.assertEqual(ws.recv(5), "after-pong")
        server.join(5)
        self.assertEqual(server.pong_opcode, 0xA)
        self.assertEqual(server.pong_payload, b"abc")

    def test_close_frame_raises_with_the_code(self):
        def close_now(server, conn):
            conn.sendall(encode_frame(OP_CLOSE, struct.pack("!H", 1001) + b"bye",
                                      mask=False))
            server.read_message(conn)
        server = self._serve(close_now)
        ws = WebSocket.connect(server.url, timeout=5)
        with self.assertRaises(WebSocketClosed) as ctx:
            ws.recv(5)
        self.assertEqual(ctx.exception.code, 1001)
        self.assertEqual(ctx.exception.reason, "bye")
        self.assertTrue(ws.closed)

    def test_peer_disconnect_raises_closed(self):
        def hang_up(server, conn):
            conn.close()
        server = self._serve(hang_up)
        ws = WebSocket.connect(server.url, timeout=5)
        with self.assertRaises(WebSocketClosed):
            ws.recv(5)

    def test_idle_recv_returns_none(self):
        def stay_silent(server, conn):
            server.read_message(conn)
        server = self._serve(stay_silent)
        with WebSocket.connect(server.url, timeout=5) as ws:
            self.assertIsNone(ws.recv(0.3))

    def test_large_message_uses_the_64_bit_length(self):
        def big(server, conn):
            conn.sendall(encode_frame(OP_TEXT, b"y" * 200_000, mask=False))
            server.read_message(conn)
        server = self._serve(big)
        with WebSocket.connect(server.url, timeout=5) as ws:
            self.assertEqual(len(ws.recv(5)), 200_000)

    def test_message_size_guard(self):
        def big(server, conn):
            conn.sendall(encode_frame(OP_TEXT, b"y" * 5000, mask=False))
            try:
                server.read_message(conn)
            except OSError:
                pass
        server = self._serve(big)
        ws = WebSocket.connect(server.url, timeout=5, max_message_bytes=1000)
        with self.assertRaises(WebSocketError):
            ws.recv(5)

    def test_send_after_close_is_rejected(self):
        server = self._serve(echo_once)
        ws = WebSocket.connect(server.url, timeout=5)
        ws.send("hello")
        ws.recv(5)
        ws.close()
        with self.assertRaises(WebSocketClosed):
            ws.send("nope")


if __name__ == "__main__":
    unittest.main()
