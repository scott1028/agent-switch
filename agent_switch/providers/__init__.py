# SPDX-License-Identifier: AGPL-3.0-only
# Copyright 2026-present the agent-switch authors. See LICENSE.

"""Model-server providers: detection, model resolution and request-body translation.

Each provider answers the same questions: which models it serves, each one's runtime
window, how to load one, and which request fields it accepts.
"""

from pathlib import PurePosixPath
from typing import Optional

from agent_switch.providers import llamacpp, lmstudio, ollama, openai, vllm
from agent_switch.providers.types import ProviderError, Target
from agent_switch.providers.utils import endpoint_exists, request_json

# The native adapters are deliberate, independent HTTP integrations: the shared OpenAI-compatible
# API does not uniformly say which models are loaded, their runtime window, how to load one, or
# which request extensions a server reads. "openai" is the generic fallback for any other server.
PROVIDERS = ("ollama", "lmstudio", "llamacpp", "vllm", "openai")
_MODULES = {
    "ollama": ollama,
    "lmstudio": lmstudio,
    "llamacpp": llamacpp,
    "vllm": vllm,
    "openai": openai,
}
# Checked in this order: each fingerprint is specific enough to skip the ones after it.
# Native servers also answer /v1/models, so the generic fallback applies only when none matches.
_DETECT_ORDER = ("ollama", "lmstudio", "llamacpp", "vllm")
# Placeholder for servers without auth: every agent refuses an empty key.
NO_KEY = "agent-switch"
# Default agent window when the server reports none, and cap when it reports more.
DEFAULT_CONTEXT_LENGTH = 250_000
# Keep the server window as is, or leave it unset so the agent uses its default.
SERVER_CONTEXT_LENGTH = -1
_NEEDS = {
    "/v1/messages": "an Anthropic-compatible /v1/messages endpoint, which Claude Code needs",
    "/v1/responses": "a /v1/responses endpoint, which Codex needs (it no longer speaks Chat Completions)",
}


def label(name: str) -> str:
    return _MODULES[name].LABEL


def root_url(url: str) -> str:
    """http://host:port[/v1][/] -> http://host:port"""
    base = url.strip().rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def detect(base: str, key: Optional[str] = None, headers: Optional[dict] = None) -> Optional[str]:
    """The provider serving `base`, or None when nothing answers there."""
    try:
        status, _ = request_json("GET", f"{base}/v1/models", key, timeout = 3, headers = headers)
    except OSError:
        return None
    for name in _DETECT_ORDER:
        if _MODULES[name].fingerprint(base, key, headers):
            return name
    return "openai" if status in (200, 401, 403) else None


def resolve_target(
    url: Optional[str],
    provider: Optional[str],
    key: Optional[str] = None,
    headers: Optional[dict] = None,
) -> Optional[Target]:
    """The target named by --url/--provider; None when neither was given."""
    if url:
        base = root_url(url)
        if provider:
            return Target(provider, base, headers or {})
        # With the key: a server behind auth hides the listings that tell providers apart.
        name = detect(base, key, headers)
        if name is None:
            raise ProviderError(f"Couldn't reach a model server at {url}.")
        return Target(name, base, headers or {})
    if provider == "openai":
        raise ProviderError("--provider openai has no usual port; pass the server with --url.")
    if provider:
        return Target(provider, _MODULES[provider].DEFAULT_URL, headers or {})
    return None


def _same_model(entry: dict, wanted: str, module) -> bool:
    canonical = getattr(module, "canonical", lambda value: value)
    for name in (entry["id"], *entry.get("aliases", [])):
        if name == wanted or canonical(name) == canonical(wanted):
            return True
        if name.casefold() == wanted.casefold():
            return True
        # llama-server names a single model by its file path.
        if "/" in name and PurePosixPath(name).name in (wanted, f"{wanted}.gguf"):
            return True
    return False


def _find(entries: list, wanted: str, module) -> Optional[dict]:
    return next((entry for entry in entries if _same_model(entry, wanted, module)), None)


def _cap_window(window: Optional[int]) -> int:
    return min(int(window), DEFAULT_CONTEXT_LENGTH) if window else DEFAULT_CONTEXT_LENGTH


def connect(
    target: Target,
    api_key: Optional[str],
    model: Optional[str],
    context_length: Optional[int],
    needs: tuple = (),
    allow_load: bool = True,
) -> tuple:
    """(base, key, entry) for a target; entry is {"id", "context_length"}."""
    keeps_server_window = context_length == SERVER_CONTEXT_LENGTH
    if keeps_server_window:
        context_length = None
    module = _MODULES[target.name]
    base, key, headers = target.base, api_key or None, target.headers
    entries = module.models(base, key, headers)
    if not model:
        loaded = [entry for entry in entries if entry["loaded"]]
        if loaded:
            match = loaded[0]
        elif entries:
            model = entries[0]["id"]
        else:
            raise ProviderError(f"{module.LABEL} at {base} lists no models.")
    if model:
        match = _find(entries, model, module)
        stale_window = (
            module.SETS_CONTEXT
            and context_length
            and match is not None
            and match.get("context_length") != context_length
        )
        if match is None or not match["loaded"] or stale_window:
            # A resident ctx alias (Ollama) already carries the requested window, so attach to it instead of loading.
            match = getattr(module, "resident_alias", lambda *a: None)(entries, model, context_length) or match
            # Recompute staleness on the (possibly alias-replaced) match: a resident model at a
            # different window still needs a reload, or --context-length would be silently ignored.
            needs_load = (
                match is None
                or not match["loaded"]
                or (
                    module.SETS_CONTEXT
                    and context_length
                    and match.get("context_length") != context_length
                )
            )
            if (
                needs_load
                and not allow_load
                and module.CAN_LOAD
                and getattr(module, "can_load", lambda *a: True)(base, key, headers)
            ):
                resident = [entry["id"] for entry in entries if entry["loaded"]]
                candidates = ", ".join(resident) if resident else "none"
                raise ProviderError(
                    f"--no-model-load: {module.LABEL} at {base} does not have {model} "
                    "loaded with these settings. "
                    f"Loaded: {candidates}. Load it on the server or drop --no-model-load."
                )
            if needs_load:
                loaded_id = module.load(base, key, model, context_length if module.SETS_CONTEXT else None, headers)
                entries = module.models(base, key, headers)
                match = _find(entries, loaded_id, module)
                if match is None or not match["loaded"]:
                    raise ProviderError(f"{module.LABEL} didn't report {model} as loaded.")
    window = match.get("context_length")
    if context_length and not module.SETS_CONTEXT:
        if window and context_length > window:
            raise ProviderError(
                f"--context-length {context_length:,} is more than the {window:,} tokens "
                f"{module.LABEL} serves {match['id']} with."
            )
        window = context_length
    if not context_length and not keeps_server_window:
        window = _cap_window(window)
    if not window and not keeps_server_window:
        raise ProviderError(
            f"Couldn't find {match['id']}'s context length on {module.LABEL} at {base}. "
            "Pass the server's real window with --context-length <tokens>."
        )
    for path in needs:
        if not endpoint_exists(base, path, key, headers):
            raise ProviderError(f"{module.LABEL} at {base} has no {_NEEDS.get(path, path)}.")
    return base, key or NO_KEY, {"id": match["id"], "context_length": int(window) if window else None}


def _pick_server(targets: tuple, listings: list, model: Optional[str]) -> tuple:
    """(index of the server --model names, the id to ask that server for)."""
    if not model:
        return 0, None
    prefix, colon, rest = model.partition(":")
    names = {target.server: index for index, target in enumerate(targets)}
    if colon and prefix in names:
        return names[prefix], rest
    matches = []
    for index, (target, entries) in enumerate(zip(targets, listings)):
        match = _find(entries, model, _MODULES[target.name])
        if match is not None:
            matches.append((index, match["id"]))
    if len(matches) == 1:
        return matches[0][0], model
    if matches:
        candidates = ", ".join(f"{targets[index].server}:{model_id}" for index, model_id in matches)
        raise ProviderError(
            f"{model} is served by more than one server: {candidates}. "
            f"Name the one you want, e.g. --model {targets[matches[0][0]].server}:{model}."
        )
    # Nothing lists it, so the first server keeps today's single --url flow, load attempt included.
    return 0, model


def connect_servers(
    targets: tuple,
    keys: list,
    model: Optional[str],
    context_length: Optional[int],
    needs: tuple = (),
    allow_load: bool = True,
) -> tuple:
    """(selected target, entry, routes) for two or more --url servers.

    routes maps each public NAME:ID to (Target, that server's own id, its key, its window), so
    the proxy can send a request to the server that serves it. entry["id"] is the public id.
    """
    listings = []
    for target, key in zip(targets, keys):
        try:
            listings.append(_MODULES[target.name].models(target.base, key, target.headers))
        except ProviderError as exc:
            raise ProviderError(f"{target.server}: {exc}") from None
    index, wanted = _pick_server(targets, listings, model)
    selected = targets[index]
    _, _, entry = connect(selected, keys[index], wanted, context_length, allow_load = allow_load)
    for target, key in zip(targets, keys):
        for path in needs:
            if not endpoint_exists(target.base, path, key, target.headers):
                raise ProviderError(
                    f"{target.server} at {target.base} has no {_NEEDS.get(path, path)}."
                )
    keeps_server_window = context_length == SERVER_CONTEXT_LENGTH
    routes: dict = {}
    for target, key, entries in zip(targets, keys, listings):
        for listed in entries:
            window = listed.get("context_length")
            if keeps_server_window:
                window = int(window) if window else None
            else:
                window = _cap_window(window)
            routes[f"{target.server}:{listed['id']}"] = (target, listed["id"], key, window)
    # A model connect just loaded is not in the listing taken above.
    public_id = f"{selected.server}:{entry['id']}"
    routes[public_id] = (selected, entry["id"], keys[index], entry["context_length"])
    entry["id"] = public_id
    return selected, entry, routes


def get_served_models(
    target: Target, api_key: Optional[str], context_length: Optional[int] = None
) -> list:
    """Listed models with agent windows in server order."""
    entries = _MODULES[target.name].models(target.base, api_key or None, target.headers)
    served_models = []
    for entry in entries:
        window = entry.get("context_length")
        if context_length == SERVER_CONTEXT_LENGTH:
            window = int(window) if window else None
        else:
            window = _cap_window(window)
        served_models.append({"id": entry["id"], "context_length": window})
    return served_models


def get_has_template_kwargs(name: str) -> bool:
    """Whether this provider reads chat_template_kwargs from a request."""
    return getattr(_MODULES[name], "TEMPLATE_KWARGS", False)


def request_body(name: str, body: dict) -> tuple:
    """(body this provider accepts, request fields it had to drop)."""
    module = _MODULES[name]
    supported = getattr(module, "SUPPORTED_FIELDS", None)
    renamed = getattr(module, "RENAMED_FIELDS", {})
    template_kwargs = getattr(module, "TEMPLATE_KWARGS", False)
    translated, dropped = {}, []
    for field, value in body.items():
        if supported is not None and field not in supported:
            dropped.append(field)
        elif field == "enable_thinking" and template_kwargs:
            translated.setdefault("chat_template_kwargs", {})["enable_thinking"] = value
        else:
            translated[renamed.get(field, field)] = value
    return translated, dropped
