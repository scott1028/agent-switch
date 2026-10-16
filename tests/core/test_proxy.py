# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""The multi-server proxy: merged listings, model routing, session token and error relaying."""

import http.client
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_switch.core.proxy import start_proxy
from agent_switch.providers.types import Target
from tests.providers.conftest import FakeServer


@pytest.fixture
def server_pair():
    pair = [FakeServer(), FakeServer()]
    yield pair
    for server in pair:
        server.close()


def _start(a: FakeServer, b: FakeServer) -> tuple:
    """A proxy over two servers that both list qwen3 under their own key and header."""
    target_a = Target("openai", a.base, {"X-Tenant": "eng"}, server = "a")
    target_b = Target("openai", b.base, {"X-Tenant": "sales"}, server = "b")
    routes = {
        "a:qwen3": (target_a, "qwen3", "key-a", 8192),
        "b:qwen3": (target_b, "qwen3", "key-b", 4096),
    }
    return start_proxy(routes, (target_a, "key-a"))


def _open(method, url, headers = None, payload = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data = data, headers = headers or {}, method = method)
    return urllib.request.urlopen(request, timeout = 10)


def _get(url, token):
    with _open("GET", url, {"Authorization": f"Bearer {token}"}) as response:
        return json.loads(response.read())


def _post(url, token, payload):
    with _open("POST", url, {"Authorization": f"Bearer {token}"}, payload) as response:
        return json.loads(response.read())


class _RawUpstream:
    """Answers every POST with exactly the headers given; FakeServer cannot choose its own."""

    def __init__(self, response_headers: list):
        self.response_headers = response_headers
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            # HTTP/1.0, so closing the connection is what ends the body.
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                # Drain the request body before answering.
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                for name, value in upstream.response_headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target = self._server.serve_forever, args = (0.05,), daemon = True).start()

    def close(self):
        self._server.shutdown()


def test_models_listing_merges_every_server_under_its_prefix(server_pair):
    base, token = _start(*server_pair)
    listing = _get(f"{base}/v1/models", token)
    assert listing["object"] == "list"
    assert [(m["id"], m["object"], m["owned_by"], m["context_length"]) for m in listing["data"]] == [
        ("a:qwen3", "model", "a", 8192),
        ("b:qwen3", "model", "b", 4096),
    ]


def test_a_prefixed_model_reaches_only_its_server(server_pair):
    a, b = server_pair
    b.route("POST", "/v1/chat/completions", body = {"choices": [{"message": {"content": "hi"}}]})
    base, token = _start(a, b)
    answer = _post(f"{base}/v1/chat/completions", token, {"model": "b:qwen3", "messages": []})
    assert answer["choices"][0]["message"]["content"] == "hi"
    # The agent's own id is the public one; the server only ever sees its own.
    assert b.requests == [("POST", "/v1/chat/completions", {"model": "qwen3", "messages": []}, "Bearer key-b")]
    assert not a.requests
    sent = b.header_logs[-1][2]
    assert sent["X-Tenant"] == "sales"
    assert sent["User-Agent"] == "agent-switch"


def test_the_clients_own_framing_headers_do_not_travel(server_pair):
    a, b = server_pair
    b.route("POST", "/v1/chat/completions", body = {"ok": True})
    base, token = _start(a, b)
    with _open(
        "POST",
        f"{base}/v1/chat/completions",
        {"Authorization": f"Bearer {token}", "Accept-Encoding": "gzip", "X-Trace": "keep"},
        {"model": "b:qwen3"},
    ) as response:
        assert response.status == 200
    sent = {name.lower(): value for name, value in b.header_logs[-1][2].items()}
    # A gzipped body could never stream, so the proxy asks for its own encoding, and it keeps
    # the agent's other headers.
    assert sent["accept-encoding"] == "identity"
    assert sent["x-trace"] == "keep"


def test_a_client_connection_header_and_what_it_names_stop_at_the_proxy(server_pair):
    a, b = server_pair
    b.route("POST", "/v1/chat/completions", body = {"ok": True})
    base, token = _start(a, b)
    # urllib rewrites Connection, so this request goes through http.client instead.
    host, _, port = base[len("http://"):].partition(":")
    connection = http.client.HTTPConnection(host, int(port), timeout = 10)
    connection.request(
        "POST",
        "/v1/chat/completions",
        body = json.dumps({"model": "b:qwen3"}),
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connection": "X-Client-Hop",
            "X-Client-Hop": "1",
            "Proxy-Connection": "keep-alive",
            "X-Kept": "keep",
        },
    )
    try:
        assert connection.getresponse().status == 200
    finally:
        connection.close()
    sent = {name.lower(): value for name, value in b.header_logs[-1][2].items()}
    # RFC 9110 7.6.1: Connection names headers that must not travel past this hop. The only
    # Connection upstream sees is the one urllib sets for its own connection.
    assert sent["connection"] == "close"
    assert "x-client-hop" not in sent
    assert "proxy-connection" not in sent
    assert sent["x-kept"] == "keep"


def test_an_upstreams_hop_by_hop_response_headers_stop_at_the_proxy():
    # FakeServer cannot answer with its own headers, so this upstream writes the raw response.
    upstream = _RawUpstream(
        [
            ("Connection", "X-Upstream-Hop"),
            ("X-Upstream-Hop", "1"),
            ("Trailer", "X-Checksum"),
            ("X-Kept", "keep"),
        ]
    )
    target = Target("openai", upstream.base, server = "raw")
    base, token = start_proxy({"raw:x": (target, "x", "key-x", 1024)}, (target, "key-x"))
    try:
        with _open(
            "POST", f"{base}/v1/chat/completions", {"Authorization": f"Bearer {token}"}, {"model": "raw:x"}
        ) as response:
            assert response.headers["X-Kept"] == "keep"
            # The body travels de-chunked, so an announced Trailer could never arrive anyway.
            for name in ("Connection", "X-Upstream-Hop", "Trailer"):
                assert name not in response.headers
    finally:
        upstream.close()


def test_an_unknown_model_goes_to_the_first_server_unchanged(server_pair):
    a, b = server_pair
    a.route("POST", "/v1/chat/completions", body = {"ok": True})
    base, token = _start(a, b)
    _post(f"{base}/v1/chat/completions", token, {"model": "other", "messages": []})
    assert a.requests == [("POST", "/v1/chat/completions", {"model": "other", "messages": []}, "Bearer key-a")]
    assert not b.requests


def test_a_request_without_the_session_token_is_refused(server_pair):
    a, b = server_pair
    base, _token = _start(a, b)
    for headers in ({}, {"Authorization": "Bearer nope"}, {"x-api-key": "nope"}):
        with pytest.raises(urllib.error.HTTPError) as raised:
            _open("GET", f"{base}/v1/models", headers)
        assert raised.value.code == 401
    assert not a.requests and not b.requests


def test_the_session_token_passes_as_x_api_key_too(server_pair):
    base, token = _start(*server_pair)
    with _open("GET", f"{base}/v1/models", {"x-api-key": token}) as response:
        assert response.status == 200


def test_an_upstream_error_status_and_body_travel_back(server_pair):
    a, b = server_pair
    b.route("POST", "/v1/chat/completions", status = 400, body = {"error": {"message": "bad model"}})
    base, token = _start(a, b)
    with pytest.raises(urllib.error.HTTPError) as raised:
        _post(f"{base}/v1/chat/completions", token, {"model": "b:qwen3"})
    assert raised.value.code == 400
    assert json.loads(raised.value.read())["error"]["message"] == "bad model"


def test_an_unreachable_server_answers_502_naming_it(server_pair):
    dead = Target("openai", "http://127.0.0.1:9", server = "gone")
    base, token = start_proxy({"gone:x": (dead, "x", "key-x", 1024)}, (dead, "key-x"))
    with pytest.raises(urllib.error.HTTPError) as raised:
        _post(f"{base}/v1/chat/completions", token, {"model": "gone:x"})
    assert raised.value.code == 502
    assert "gone" in json.loads(raised.value.read())["error"]["message"]
