"""A neutral test endpoint and two local authenticated HTTP proxies, for demonstrating
health checks, rotation, failover and challenge handling without touching any social
platform. Swap in GAJAN's approved proxies and neutral endpoint via config and vault.
"""
from __future__ import annotations

import base64
import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class _Endpoint(BaseHTTPRequestHandler):
    def do_GET(self):
        via = self.headers.get("X-Test-Proxy", "direct")
        if self.path.startswith("/challenge"):
            body, code = {"error": "verify_identity", "message": "security challenge"}, 403
        else:
            body, code = {"ok": True, "via": via, "path": self.path}, 200
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class NeutralEndpoint:
    def __init__(self, port: int = 8899):
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _Endpoint)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class TestProxy:
    """Minimal forward proxy for plain-HTTP targets with Basic proxy authentication."""
    __test__ = False   # not a pytest test class

    def __init__(self, name: str, user: str, password: str, port: int = 0):
        self.name = name
        expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        proxy = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = self.connection.recv(4096)
                    if not chunk:
                        return
                    head += chunk
                header_blob, _, rest = head.partition(b"\r\n\r\n")
                lines = header_blob.decode("latin-1").split("\r\n")
                method, target, version = lines[0].split(" ", 2)
                headers = dict(l.split(": ", 1) for l in lines[1:] if ": " in l)
                if headers.get("Proxy-Authorization") != expected:
                    self.connection.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n"
                                            b"Connection: close\r\n\r\n")
                    return
                u = urlsplit(target)
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                fwd = {k: v for k, v in headers.items() if k.lower() not in ("proxy-authorization", "proxy-connection")}
                fwd["X-Test-Proxy"] = proxy.name
                fwd["Connection"] = "close"
                req = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fwd.items()) + "\r\n"
                with socket.create_connection((u.hostname, u.port or 80), timeout=5) as up:
                    up.sendall(req.encode("latin-1") + rest)
                    while True:
                        data = up.recv(65536)
                        if not data:
                            break
                        self.connection.sendall(data)

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, user: str, password: str) -> str:
        return f"http://{user}:{password}@127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
