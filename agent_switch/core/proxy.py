# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""A 127.0.0.1 reverse proxy that puts every --url server behind one base URL."""

import json
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent_switch._inference import _USER_AGENT, urlopen_no_redirect
from agent_switch.providers.utils import get_has_custom_authorization

# Bounds one upstream read; a long generation streams, so this is not a turn limit.
_UPSTREAM_TIMEOUT_S = 600

# Hop-by-hop headers, plus the ones the upstream request gets from this proxy instead.
_DROP_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "authorization",
        "x-api-key",
        "host",
        "content-length",
        "accept-encoding",
    }
)

# The response ends when the connection closes, so upstream's own framing never travels on.
# A Trailer header is dropped too: the body is relayed de-chunked, so a trailer never arrives.
_DROP_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
)


def _get_connection_hops(headers) -> frozenset:
    """The lowercased names that message's own Connection header makes hop-by-hop (RFC 9110 7.6.1)."""
    # get_all returns None, not an empty list, when the header is absent.
    return frozenset(
        token.strip().lower()
        for value in headers.get_all("Connection") or ()
        for token in value.split(",")
        if token.strip()
    )


def start_proxy(routes: dict, primary: tuple) -> tuple:
    """Serve every server in routes through one base; returns (base, session token).

    routes maps a public NAME:ID to (Target, that server's own id, its key, its window);
    primary is the (Target, key) a request with no known model goes to. The proxy needs no
    shutdown: _launch waits for the agent, and agent-switch exiting takes the daemon thread
    serving it with the rest of the process.
    """
    token = secrets.token_urlsafe()
    server = _QuietServer(("127.0.0.1", 0), _proxy_handler(routes, primary, token))
    threading.Thread(target = server.serve_forever, args = (0.05,), daemon = True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", token


class _QuietServer(ThreadingHTTPServer):
    """A request thread never outlives the session, and nothing it does reaches the terminal."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        # An agent that hangs up mid-request is normal here; a traceback would corrupt its TUI.
        pass


def _json_object(body: bytes) -> object:
    """The body as a JSON object, else None."""
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _model_listing(routes: dict) -> dict:
    """Every server's models in one listing, each under its public id."""
    return {
        "object": "list",
        "data": [
            {"id": public_id, "object": "model", "owned_by": target.server, "context_length": window}
            for public_id, (target, _model_id, _key, window) in routes.items()
        ],
    }


def _proxy_handler(routes: dict, primary: tuple, token: str):
    """The handler class, closed over this session's routes, first server and token."""

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0, so the close of the connection is what ends a streamed response.
        protocol_version = "HTTP/1.0"

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def log_message(self, *args):
            pass

        def _serve(self, method: str) -> None:
            try:
                if not self._has_token():
                    self._send_json(401, _error("missing or wrong agent-switch session token"))
                    return
                if method == "GET" and self.path.split("?", 1)[0] == "/v1/models":
                    self._send_json(200, _model_listing(routes))
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                payload = _json_object(body)
                model = payload.get("model") if payload is not None else None
                route = routes.get(model) if isinstance(model, str) else None
                if route is not None:
                    target, upstream_id, key, _window = route
                    payload["model"] = upstream_id
                    body = json.dumps(payload).encode()
                else:
                    target, key = primary
                self._forward(method, body, target, key)
            except OSError:
                pass  # the agent hung up mid-request

        def _has_token(self) -> bool:
            """Whether the request carries this session's token as its key.

            Without it, another process on this machine could use the servers' keys all session.
            """
            given = self.headers.get("Authorization") or ""
            if given[:7].casefold() == "bearer ":
                given = given[7:].strip()
            # Claude Code (ANTHROPIC_AUTH_TOKEN), the OpenAI SDK and Codex send the bearer; x-api-key is accepted too.
            wanted = token.encode()
            for candidate in (given, self.headers.get("x-api-key") or ""):
                if secrets.compare_digest(candidate.encode("utf-8", "replace"), wanted):
                    return True
            return False

        def _forward(self, method: str, body: bytes, target, key) -> None:
            hops = _get_connection_hops(self.headers)
            headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() not in _DROP_REQUEST_HEADERS | hops
            }
            headers["User-Agent"] = _USER_AGENT
            # A server's own Authorization header from --header replaces the Bearer <key>.
            if key and not get_has_custom_authorization(target.headers):
                headers["Authorization"] = f"Bearer {key}"
            headers.update(target.headers)
            request = urllib.request.Request(
                f"{target.base}{self.path}", data = body or None, headers = headers, method = method
            )
            try:
                # No redirects: a 3xx would hand this server's key to a base nobody vetted.
                response = urlopen_no_redirect(request, _UPSTREAM_TIMEOUT_S)
            except urllib.error.HTTPError as exc:
                self._relay(exc.code, exc.headers, exc.fp)
                return
            except OSError as exc:
                self._send_json(
                    502,
                    _error(f"could not reach {target.server} at {target.base}: {exc}"),
                )
                return
            self._relay(response.status, response.headers, response)

        def _relay(self, status: int, headers, fp) -> None:
            hops = _get_connection_hops(headers)
            self.send_response(status)
            for name, value in headers.items():
                if name.lower() not in _DROP_RESPONSE_HEADERS | hops:
                    self.send_header(name, value)
            self.end_headers()
            if fp is None:
                return
            # Read and write as it arrives, so SSE tokens reach the agent as they are produced.
            while True:
                chunk = fp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            fp.close()

        def _send_json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _error(message: str) -> dict:
    """An OpenAI-shaped error body, the one every agent here already reads."""
    return {"error": {"message": f"agent-switch proxy: {message}"}}
