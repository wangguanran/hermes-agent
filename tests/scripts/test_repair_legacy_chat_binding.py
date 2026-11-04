"""Explicit, offline renewal after a same-profile home move; synthetic credentials only."""
import copy
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway.session_policy import bind_launch_key, build_policy, restore_policy
from gateway.session_policy_credentials import recover_config_secrets
from hermes_state_runtime import RuntimeStoreError


def fixture_binding(tmp_path):
    old_home, home = tmp_path / 'old-profile', tmp_path / 'current-profile'
    old_home.mkdir()
    home.mkdir()
    cwd = home / 'workspace'
    cwd.mkdir()
    config = {'model': {'default': 'fixture', 'provider': 'custom',
                        'base_url': 'http://127.0.0.1:9/v1'},
              'voice': {'record_key': 'synthetic-voice-key'},
              'platform_toolsets': {'gui': ['terminal']},
              'terminal': {'backend': 'local'}}
    (home / 'config.yaml').write_text(json.dumps(config))
    sid = 'fixture-legacy-chat'
    old_authority = SimpleNamespace(db=SimpleNamespace(db_path=old_home / 'state.db'),
                                    profile_id=str(old_home), instance_id='old', epoch=1)
    private = {}
    policy = build_policy({'cwd': str(old_home), 'source': 'gui', 'follow_profile_config': True},
                          copy.deepcopy(config), private_secrets=private)
    policy = replace(policy, model='fixture')
    policy = bind_launch_key(old_authority, sid, policy, None, config_secrets=private)
    receipt = {'session_id': sid, 'legacy_session_id': sid, 'profile_id': str(home),
               'principal_id': f'uid:{os.getuid()}', 'request_id': f'legacy:{sid}',
               'policy': asdict(policy)}
    authority = SimpleNamespace(db=SimpleNamespace(db_path=home / 'state.db'),
                                profile_id=str(home), instance_id='maintenance', epoch=2)
    old_home.rmdir()
    return receipt, authority, config, old_home, cwd


def test_renewed_binding_uses_verified_current_credential_without_changing_identity(tmp_path):
    from scripts.repair_legacy_chat_binding import renew_policy
    receipt, authority, config, old_home, cwd = fixture_binding(tmp_path)
    before = copy.deepcopy(receipt)
    old = restore_policy(receipt['policy'])
    with pytest.raises(RuntimeStoreError, match='launch_credentials_unavailable') as refused:
        recover_config_secrets(authority, old)
    assert refused.value.field == 'home'
    renewed = renew_policy(receipt, authority, config, old_home=str(old_home), cwd=str(cwd))
    policy = restore_policy(renewed)
    assert policy.cwd == str(cwd)
    assert json.loads(policy.terminal_json)['TERMINAL_CWD'] == str(cwd)
    assert recover_config_secrets(authority, policy)[('voice', 'record_key')] == 'synthetic-voice-key'
    assert policy.request_json == old.request_json
    assert policy.config_json == old.config_json
    assert policy.model == old.model and policy.toolsets == old.toolsets
    assert receipt == before


@pytest.mark.parametrize('change', ['principal', 'profile', 'native', 'credential', 'endpoint', 'reference'])
def test_foreign_identity_or_unverified_credentials_are_never_renewed(tmp_path, change):
    from scripts.repair_legacy_chat_binding import renew_policy
    receipt, authority, config, old_home, cwd = fixture_binding(tmp_path)
    if change == 'principal':
        receipt['principal_id'] = 'uid:999999'
    elif change == 'profile':
        receipt['profile_id'] = 'another-profile'
    elif change == 'native':
        receipt.pop('legacy_session_id')
    elif change == 'credential':
        config['voice']['record_key'] = 'different-synthetic-key'
    elif change == 'endpoint':
        config['voice']['url'] = 'http://attacker.invalid'
    else:
        ref = json.loads(receipt['policy']['config_secret_ref'].split(':', 1)[1])
        ref['home'] = str(tmp_path / 'unrelated-home')
        receipt['policy']['config_secret_ref'] = 'profile-config-v1:' + json.dumps(ref)
    before = copy.deepcopy(receipt)
    with pytest.raises(RuntimeStoreError, match='binding_renewal_refused'):
        renew_policy(receipt, authority, config, old_home=str(old_home), cwd=str(cwd))
    assert receipt == before


def test_live_owner_is_refused_before_opening_the_database(tmp_path):
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership
    from scripts.repair_legacy_chat_binding import repair_home_binding
    home = tmp_path / 'owned-profile'
    home.mkdir()
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        with pytest.raises(OwnershipConflict):
            repair_home_binding(home, session_id='fixture', old_home=str(tmp_path / 'old'),
                                cwd=str(home / 'workspace'), apply=True)
        assert not (home / 'state.db').exists()
    finally:
        owner.close()


def test_history_digest_handles_native_binary_sidecars_without_reencoding_them(tmp_path):
    import sqlite3
    from scripts.repair_legacy_chat_binding import _history_digest
    db = sqlite3.connect(':memory:')
    try:
        db.execute('CREATE TABLE messages(id INTEGER,session_id TEXT,sidecar BLOB)')
        db.execute('INSERT INTO messages VALUES(1,?,?)', ('fixture', b'\x00\xffnative-sidecar'))
        before = _history_digest(db, ('fixture',))
        assert before[0] == 1 and before == _history_digest(db, ('fixture',))
        db.execute('UPDATE messages SET sidecar=?', ('00ffnative-sidecar',))
        assert _history_digest(db, ('fixture',)) != before
    finally:
        db.close()


def test_low_level_apply_also_refuses_a_live_owner(tmp_path):
    from gateway.runtime_ownership import OwnershipConflict, ProfileOwnership
    from scripts.repair_legacy_chat_binding import apply_prepared
    home = tmp_path / 'owned-store'
    home.mkdir()
    writes = []
    db = SimpleNamespace(db_path=home / 'state.db', _execute_write=lambda writer: writes.append(writer))
    owner = ProfileOwnership()
    owner.reserve([home])
    try:
        with pytest.raises(OwnershipConflict):
            apply_prepared(db, home, {}, session_id='fixture', backup_path=tmp_path / 'unused')
        assert not writes
    finally:
        owner.close()


def test_repeating_a_verified_renewal_is_a_noop(tmp_path):
    from scripts.repair_legacy_chat_binding import renew_policy
    receipt, authority, config, old_home, cwd = fixture_binding(tmp_path)
    renewed = renew_policy(receipt, authority, config, old_home=str(old_home), cwd=str(cwd))
    receipt['policy'] = renewed
    assert renew_policy(receipt, authority, config, old_home=str(old_home), cwd=str(cwd)) == renewed


def test_offline_entrypoint_backs_up_and_commits_once(tmp_path):
    from datetime import datetime
    from gateway.config import Platform
    from gateway.runtime_ownership import exclusive_maintenance
    from gateway.session import SessionEntry, SessionSource
    from hermes_state import SessionDB
    from hermes_state_local import commit_local_session, local_receipt
    from hermes_state_runtime import begin_runtime_epoch
    from scripts.repair_legacy_chat_binding import repair_home_binding

    receipt, _authority, _config, old_home, cwd = fixture_binding(tmp_path)
    home, sid = cwd.parent, receipt['session_id']
    route = f'agent:main:local:dm:{sid}'
    source = SessionSource(platform=Platform.LOCAL, chat_id=sid,
                           user_id=receipt['principal_id'], chat_type='dm')
    now = datetime.now()
    entry = SessionEntry(route, sid, now, now, origin=source, platform=Platform.LOCAL)
    receipt.update(route=route, entry=entry.to_dict(), lineage=[sid])
    with exclusive_maintenance([home], purpose='synthetic-legacy-fixture'):
        db = SessionDB(db_path=home / 'state.db')
        try:
            db.create_session(sid, source='gui', cwd=str(old_home), model='fixture')
            epoch = begin_runtime_epoch(db, instance_id='synthetic-fixture')
            commit_local_session(db, epoch=epoch, receipt=receipt)
            before = db.get_session(sid)
            assert before is not None
        finally:
            db.close()
    preview = repair_home_binding(home, session_id=sid, old_home=str(old_home), cwd=str(cwd))
    assert preview['ok'] and not preview['applied'] and not preview['already_current']
    applied = repair_home_binding(home, session_id=sid, old_home=str(old_home), cwd=str(cwd), apply=True)
    assert applied['applied'] and applied['backup']
    backup = Path(applied['backup'])
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    assert backup.parent.stat().st_mode & 0o777 == 0o700
    db = SessionDB(db_path=home / 'state.db', read_only=True)
    try:
        stored = local_receipt(db, sid)
        assert {k: v for k, v in stored.items() if k != 'policy'} == {
            k: v for k, v in receipt.items() if k != 'policy'}
        row = db.get_session(sid)
        assert row is not None
        assert row['cwd'] == str(cwd)
        assert row['runtime_generation'] == before['runtime_generation'] + 1
    finally:
        db.close()
    again = repair_home_binding(home, session_id=sid, old_home=str(old_home), cwd=str(cwd), apply=True)
    assert again['already_current'] and not again['applied'] and again['backup'] is None
