# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""Types shared by the model-server providers."""

from typing import NamedTuple, Optional


class ProviderError(Exception):
    """A user-facing failure to reach or use a model server."""


class Target(NamedTuple):
    """Which server to use."""

    name: str
    base: Optional[str] = None
    # Custom --header pairs, carried so every request to this server sends them. Never mutated.
    headers: dict = {}
    # This server's --url NAME, set only when more than one --url runs behind the proxy.
    server: Optional[str] = None
    # Every --url server in --url order, set on the first one; the proxy routes by model id.
    upstreams: tuple = ()
    # This server's key for the whole session: its --api-key, else the one remembered for it.
    api_key: Optional[str] = None
    # True when that key came from --api-key, so it is the one allowed to be remembered.
    is_given_key: bool = False
