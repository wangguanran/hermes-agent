"""Local policy: fresh profiles enable supported tools except delegation."""
from pathlib import Path


def seed_full_toolsets(profile_dir: Path) -> None:
    """Write all supported toolsets to the unpublished fresh profile staging tree.

    Platform restrictions and independently configured STT remain explicit. The policy
    configures toolsets only: it never installs plugins or supplies credentials.
    """
    from hermes_cli.config import atomic_config_write, read_user_config_raw
    from hermes_cli.tools_config import (
        PLATFORMS,
        _CONFIG_ONLY_TOOLSETS,
        _get_effective_configurable_toolsets,
    )
    from hermes_cli.toolset_scope import toolset_allowed_for_platform

    path = profile_dir / "config.yaml"
    config = read_user_config_raw(path) if path.is_file() else {}
    if not isinstance(config, dict):
        raise ValueError("fresh profile configuration is not a mapping")
    keys = {key for key, _, _ in _get_effective_configurable_toolsets()} - _CONFIG_ONLY_TOOLSETS - {"delegation"}
    if not keys:
        raise ValueError("cannot seed profile: toolset registry is empty")
    agent = config.setdefault("agent", {})
    if not isinstance(agent, dict):
        raise ValueError("fresh profile agent configuration is not a mapping")
    disabled = agent.setdefault("disabled_toolsets", [])
    if not isinstance(disabled, list):
        raise ValueError("fresh profile disabled_toolsets is not a list")
    if "delegation" not in disabled:
        disabled.append("delegation")
    config["platform_toolsets"] = {
        platform: sorted(key for key in keys if toolset_allowed_for_platform(key, platform))
        for platform in PLATFORMS
    }
    stt = config.setdefault("stt", {})
    if not isinstance(stt, dict):
        raise ValueError("fresh profile STT configuration is not a mapping")
    stt["enabled"] = True
    atomic_config_write(path, config)
