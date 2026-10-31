"""Platform-specific language tags for verbose terminal progress blocks."""

import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

from gateway.config import Platform


def _progress_mixin():
    root = Path(__file__).resolve().parents[2]
    matches = []
    for path in sorted((root / "gateway").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == "_progress_terminal_blocks"
                for child in node.body
            ):
                module_name = ".".join(path.relative_to(root).with_suffix("").parts)
                matches.append((module_name, node.name))
    if len(matches) != 1:
        raise AssertionError(f"expected one terminal progress mixin, found {len(matches)}: {matches}")
    module_name, class_name = matches[0]
    return getattr(importlib.import_module(module_name), class_name)


class _CodeBlockAdapter:
    supports_code_blocks = True


def _terminal_blocks(platform) -> tuple[str | None, str | None]:
    mixin = _progress_mixin()
    turn = object.__new__(mixin)
    turn._ctx = SimpleNamespace(
        source=SimpleNamespace(platform=platform),
        last_was_terminal_block=[False],
    )
    return turn._progress_terminal_blocks(
        _CodeBlockAdapter(), "terminal", {"command": "printf hello"}, "💻"
    )


def test_feishu_terminal_progress_uses_chinese_terminal_fence():
    full, short = _terminal_blocks(Platform.FEISHU)
    assert full is not None and short is not None
    assert "```终端\nprintf hello\n```" in full
    assert "```终端\nprintf hello\n```" in short


def test_slack_terminal_progress_keeps_untagged_fence():
    full, short = _terminal_blocks(Platform.SLACK)
    assert full is not None and short is not None
    assert "```\nprintf hello\n```" in full
    assert "```\nprintf hello\n```" in short
    assert "```终端" not in full


def test_shell_display_breaks_top_level_commands_but_preserves_quoted_text():
    from agent.display import wrap_shell_commands

    assert wrap_shell_commands("echo 'a;b' && printf ok; echo done") == (
        "echo 'a;b' &&\nprintf ok ;\necho done"
    )
    heredoc = "python3 - <<'PY'\nprint('a;b')\nPY"
    assert wrap_shell_commands(heredoc) == heredoc
