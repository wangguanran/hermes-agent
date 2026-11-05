"""Work-home opt-in routing; administrative budgets outrank frozen endpoints.

Never modify session receipts or credentials. Only the declared upstream is moved
onto the same home's loopback gate. Other installations have no opt-in file.
"""

from copy import deepcopy
from functools import wraps
import json
from pathlib import Path
from urllib.parse import urlsplit


_HEADER = "X-Hermes-Budget-Profile"


def _context(home=None):
    if home is None:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    home = Path(home).resolve()
    root = home.parent.parent if home.parent.name == "profiles" else home
    path = root / "service/meig-budget.json"
    if not path.exists():
        return None
    settings = json.loads(path.read_text())
    if settings.get("enabled") is False:
        return None
    if settings.get("enabled") is not True:
        raise ValueError("Invalid work budget opt-in")
    if Path(settings["hermes_home"]).resolve() != root:
        raise ValueError("Work budget home mismatch")
    upstream = urlsplit(settings["upstream"])
    if (
        upstream.scheme not in {"http", "https"}
        or not upstream.hostname
        or upstream.username
        or upstream.password
        or upstream.query
        or upstream.fragment
        or upstream.path not in {"", "/"}
    ):
        raise ValueError("Invalid work budget upstream")
    port = settings["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid work budget loopback port")
    profile = "default" if home == root else home.name
    return upstream, f"http://127.0.0.1:{port}/v1", profile


def _matches(value, upstream):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)

    def origin(url):
        return (
            url.scheme,
            url.hostname,
            url.port or (443 if url.scheme == "https" else 80),
        )

    return (
        origin(parsed) == origin(upstream)
        and parsed.path.rstrip("/") in {"", "/v1"}
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
    )


def _headers(headers, profile):
    return {
        **{k: v for k, v in (headers or {}).items() if k.lower() != _HEADER.lower()},
        _HEADER: profile,
    }


def apply_runtime_budget(runtime, home=None):
    context = _context(home)
    if context is None:
        return runtime
    upstream, proxy, profile = context
    if not (
        _matches(runtime.get("base_url"), upstream)
        or runtime.get("base_url", "").rstrip("/") == proxy
    ):
        return runtime
    return {
        **runtime,
        "base_url": proxy,
        "extra_headers": _headers(runtime.get("extra_headers"), profile),
    }


def apply_budget_policy(config, home=None):
    """Rebind execution routing, not the immutable receipt or any prompt/tool data."""
    context = _context(home)
    if context is None:
        return config
    upstream, proxy, profile = context
    config = deepcopy(config)
    controlled = set()

    def retarget(entry):
        if not isinstance(entry, dict):
            return False
        matched = False
        for key in ("api", "base_url"):
            value = entry.get(key)
            if _matches(value, upstream) or (
                isinstance(value, str) and value.rstrip("/") == proxy
            ):
                entry[key] = proxy
                matched = True
        if matched:
            entry["extra_headers"] = _headers(entry.get("extra_headers"), profile)
        return matched

    for name, entry in (config.get("providers") or {}).items():
        if retarget(entry):
            controlled.add(name.lower().removeprefix("custom:"))
    for entry in config.get("custom_providers") or []:
        if retarget(entry) and isinstance(entry.get("name"), str):
            controlled.add(entry["name"].lower().removeprefix("custom:"))
    direct = retarget(config.get("model"))
    if not controlled and not direct:
        return config
    # The explicit work-home opt-in owns this chain, including old worker policies.
    # Keep each session's primary selection, credentials, tools and history intact.
    config["fallback_providers"] = [
        {"provider": "meigsmart", "model": "deepseek-v4.1-flash"},
        {"provider": "openai-codex", "model": "gpt-6.1-sol"},
    ]
    for task in (config.get("auxiliary") or {}).values():
        if not isinstance(task, dict):
            continue
        provider = str(task.get("provider") or "").lower().removeprefix("custom:")
        if retarget(task) or provider in controlled:
            task["fallback_chain"] = [
                {"provider": "openai-codex", "model": "gpt-6.1-sol"}
            ]
    return config


def budgeted_runtime_provider(resolve):
    @wraps(resolve)
    def wrapped(*args, **kwargs):
        return apply_runtime_budget(resolve(*args, **kwargs))

    return wrapped
