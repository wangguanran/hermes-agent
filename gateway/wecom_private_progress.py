"""Privacy-scoped WeCom DM progress formatting; group events never enter this path."""
from __future__ import annotations

import json
import re
from typing import Any

_HOST_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:/(?:Volumes|Users|private)(?:/[^\s\"'<>`]*)?"
    r"|/home/(?!rantu(?:/|$))[^\s\"'<>`]+)",
    re.IGNORECASE,
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\(?:Users|Documents and Settings)\\[^\s\"'<>`]*")
_SENSITIVE_COMMAND = re.compile(
    r"(?i)(?:\b(?:printenv|env|set|export|security|keychain)\b|"
    r"(?:^|[/\s'\"])(?:\.env(?:\.[\w.-]+)?|auth\.json|credentials(?:\.json)?|"
    r"id_rsa|id_ed25519)(?:$|[\s'\";|&])|/proc/(?:self|\d+)/environ)"
)
_SENSITIVE_KEY = re.compile(r"(?i)(?:token|secret|password|passwd|credential|api.?key|authorization|private.?key)")
_INLINE_SECRET = re.compile(
    r"(?i)([\"']?(?:[a-z0-9_-]*(?:token|secret|password|passwd|credential|api[_-]?key|authorization))"
    r"[\"']?\s*[:=]\s*)([\"']?)[^\s,;\"'{}]+"
)


def enabled_for_dm(ctx: Any) -> bool:
    """Only an opted-in WeCom direct message may enter this formatter."""
    try:
        source = ctx.source
        if getattr(source.platform, "value", source.platform) != "wecom" or source.chat_type != "dm":
            return False
        from utils import is_truthy_value
        config = ((ctx.user_config.get("display") or {}).get("platforms") or {}).get("wecom") or {}
        return isinstance(config, dict) and is_truthy_value(config.get("private_tool_details"), default=False)
    except (AttributeError, TypeError):
        return False


def _safe_text(value: Any) -> str:
    """Redact known credentials and host-shaped paths; fail closed on sanitizer errors."""
    try:
        from agent.redact import redact_for_egress
        text = str(value or "")
        from agent.secret_scope import current_secret_scope
        scope = current_secret_scope() or {}
        secrets = sorted(
            (str(v) for k, v in scope.items() if _SENSITIVE_KEY.search(str(k)) and len(str(v or "")) >= 6),
            key=len, reverse=True,
        )
        for secret in secrets:
            text = text.replace(secret, "[已隐藏]")
        text = redact_for_egress(text)
        text = _INLINE_SECRET.sub(lambda m: m.group(1) + m.group(2) + "[已隐藏]", text)
        text = _HOST_PATH.sub("[宿主路径已隐藏]", text)
        return _WINDOWS_PATH.sub("[宿主路径已隐藏]", text)
    except Exception:
        return "[敏感信息过滤失败，已隐藏]"


def _bounded(value: str, limit: int = 2500) -> str:
    raw = value.encode("utf-8")
    return value if len(raw) <= limit else raw[:limit].decode("utf-8", errors="ignore") + "\n…（内容过长，已截断）"


def format_event(ctx: Any, event_type: str, tool_name: str | None,
                 args: Any, kwargs: dict) -> str | None:
    """Render a paired tool start or completion, limiting raw completion to terminal tools."""
    from agent.display import fence_safe, wrap_shell_commands

    if event_type not in {"tool.started", "tool.completed"} or not tool_name or tool_name == "clarify":
        return None
    call_id = str(kwargs.get("tool_call_id") or "")
    if not hasattr(ctx, "_wecom_private_calls"):
        ctx._wecom_private_calls = {}
    pending = ctx._wecom_private_calls
    if event_type == "tool.started":
        command = args.get("command", "") if isinstance(args, dict) and tool_name == "terminal" else ""
        if call_id and tool_name == "terminal":
            pending[call_id] = bool(_SENSITIVE_COMMAND.search(str(command)))
        if command:
            details = wrap_shell_commands(str(command))
        elif args:
            details = json.dumps(args, ensure_ascii=False, default=str, indent=2)
        else:
            details = ""
        name = _bounded(_safe_text(tool_name), 100)
        text = f"🔧 {name}"
        if details:
            text += "\n```\n" + fence_safe(_bounded(_safe_text(details))) + "\n```"
        return text
    status = "失败" if kwargs.get("is_error") else "完成"
    name = _bounded(_safe_text(tool_name), 100)
    if tool_name != "terminal":
        return f"✅ {name}：{status}" if status == "完成" else f"❌ {name}：{status}"
    # The regular tool executor includes a call id only in its native callbacks;
    # its progress completion carries the command in args instead. Never guess if
    # neither a tracked id nor a completion command is present.
    if call_id:
        suppress_output = pending.pop(call_id, True)
    else:
        completed_command = args.get("command") if isinstance(args, dict) else None
        suppress_output = not completed_command or bool(_SENSITIVE_COMMAND.search(str(completed_command)))
    if suppress_output:
        return f"🔒 {name}：{status}（输出可能含敏感信息，已隐藏）"
    result = kwargs.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            result = {"output": result}
    if not isinstance(result, dict):
        result = {"output": str(result) if result is not None else ""}
    output = str(result.get("output") or "")
    error = str(result.get("error") or "")
    if error and error not in output:
        output = f"{output.rstrip()}\n{error}".strip()
    code = result.get("exit_code")
    if code not in (None, 0):
        status = f"失败（退出码 {code}）"
    safe_output = fence_safe(_bounded(_safe_text(output or "[无输出]")))
    return f"{'✅' if status == '完成' else '❌'} {name}：{status}\n```\n{safe_output}\n```"
