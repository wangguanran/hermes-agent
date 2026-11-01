"""Windows profile policy: delegation off, Kanban limited to the driver role."""
import sys
from pathlib import Path
import pytest
from hermes_cli import profiles
from hermes_cli.config import read_user_config_raw

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows-only personal policy')
def test_windows_fresh_and_cloned_profiles_obey_role_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.hermes'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(profiles, '_notify_multiplexer', lambda *_a, **_k: None)
    monkeypatch.setattr(profiles, '_maybe_register_gateway_service', lambda *_a, **_k: None)
    engineer = profiles.create_profile('driver-engineer', no_alias=True, no_skills=True)
    other = profiles.create_profile('personal-test', no_alias=True, no_skills=True)
    clone = profiles.create_profile('cloned-test', clone_from='driver-engineer', clone_config=True, no_alias=True)
    d = read_user_config_raw(engineer / 'config.yaml')
    assert 'delegation' in d['agent']['disabled_toolsets']
    assert 'kanban' in d['platform_toolsets']['cli']
    for directory in (other, clone):
        config = read_user_config_raw(directory / 'config.yaml')
        assert {'delegation', 'kanban'} <= set(config['agent']['disabled_toolsets'])
        assert all('kanban' not in choices for choices in config.get('platform_toolsets', {}).values())
        for key in ('dispatch_in_gateway', 'notify_in_gateway', 'auto_subscribe_on_create', 'review_dispatch'):
            assert config['kanban'][key] is False
