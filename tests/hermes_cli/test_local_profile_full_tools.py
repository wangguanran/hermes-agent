"""Local default: a newly created, non-cloned profile enables available toolsets."""
from pathlib import Path

from hermes_cli import profiles
from hermes_cli.config import read_user_config_raw
from hermes_cli.tools_config import CONFIGURABLE_TOOLSETS, PLATFORMS
from hermes_cli.toolset_scope import toolset_allowed_for_platform


def test_fresh_profile_enables_all_platform_toolsets_and_stt(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(profiles, "_notify_multiplexer", lambda *_a, **_k: None)
    monkeypatch.setattr(profiles, "_maybe_register_gateway_service", lambda *_a, **_k: None)
    created = profiles.create_profile("full-tools", no_alias=True, no_skills=True)
    config = read_user_config_raw(created / "config.yaml")
    assert config["stt"]["enabled"] is True
    for platform in PLATFORMS:
        actual = set(config["platform_toolsets"][platform])
        expected = {key for key, _, _ in CONFIGURABLE_TOOLSETS
                    if key not in {"stt", "delegation"} and toolset_allowed_for_platform(key, platform)}
        assert expected <= actual, f"{platform}: missing {sorted(expected - actual)}"
        assert "stt" not in actual
        assert "delegation" not in actual
    assert "delegation" in config["agent"]["disabled_toolsets"]
    assert "discord_admin" not in config["platform_toolsets"]["feishu"]
    assert "discord_admin" in config["platform_toolsets"]["discord"]
    assert not (created / "auth.json").exists()
