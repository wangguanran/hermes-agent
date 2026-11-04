#!/usr/bin/env python3
"""Operator-only renewal of a relocated, already-owned legacy Bot Chat binding.

This never changes profile/principal/lineage identity or the frozen configuration.
It re-issues a current-profile credential reference only after the *same* private
values and endpoints have been verified. Applying requires exclusive maintenance;
a live owner must be drained and stopped through its normal lifecycle first.
"""
from __future__ import annotations

import hmac
import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import NoReturn


def renew_policy(receipt, authority, config, *, old_home: str, cwd: str):
    """Prepare, without writes, a renewal from this operator's current profile source."""
    from gateway.session_policy import restore_policy
    from gateway.session_policy_credentials import PREFIX, _ABSENT, _binding, _defined, _fingerprint, config_reference
    from hermes_state_runtime import RuntimeStoreError

    def refuse(field) -> NoReturn:
        raise RuntimeStoreError('binding_renewal_refused', field=field)

    sid = receipt.get('session_id')
    home = str(Path(authority.db.db_path).resolve().parent)
    if receipt.get('legacy_session_id') != sid or receipt.get('request_id') != f'legacy:{sid}':
        refuse('legacy_identity')
    if receipt.get('profile_id') != home or authority.profile_id != home:
        refuse('profile_identity')
    if receipt.get('principal_id') != f'uid:{os.getuid()}':
        refuse('principal_identity')
    old = restore_policy(receipt['policy'])
    if old.source != 'gui' or not old.follows_profile or old.ignore_user_config or old.credential_ref:
        refuse('launch_policy')
    if not Path(old_home).is_absolute() or old_home == home or old.cwd not in (old_home, cwd):
        refuse('old_home')
    target = Path(cwd)
    if not target.is_absolute() or not target.is_dir() or str(target.resolve()) != cwd:
        refuse('cwd')
    if not isinstance(old.config_secret_ref, str) or not old.config_secret_ref.startswith(PREFIX):
        refuse('config_secret_ref')
    source = json.loads(old.config_secret_ref[len(PREFIX):])
    reference_home = home if old.cwd == cwd else old_home
    if any(source.get(k) != v for k, v in {'home': reference_home, 'profile': reference_home, 'session': sid}.items()):
        refuse('reference_identity')
    frozen = json.loads(old.config_json)
    bound = {tuple(path): digest for path, digest in source.get('bound', [])}
    private = {}
    for path, digest in source['entries']:
        key = tuple(path)
        # Terminal-scope secrets and old whole-policy-only references need a different recovery.
        if not path or path[0] is None or key not in bound or key in private:
            refuse('credential_shape')
        original_endpoint = _binding(frozen, path)
        if not hmac.compare_digest(original_endpoint, bound[key]):
            refuse('stored_endpoint_binding')
        if not hmac.compare_digest(original_endpoint, _binding(config, path)):
            refuse('current_endpoint_binding')
        value = _defined(config, path)
        fingerprint = None if value is _ABSENT else _fingerprint(value)
        if not isinstance(fingerprint, str) or not hmac.compare_digest(fingerprint, digest):
            refuse('current_credential')
        private[key] = value
    if not private:
        refuse('empty_credential_reference')
    if reference_home == home:
        return asdict(old)  # already renewed, and its current source verified again
    terminal = json.loads(old.terminal_json)
    terminal['TERMINAL_CWD'] = cwd
    renewed = replace(old, cwd=cwd, terminal_json=json.dumps(terminal))
    return asdict(replace(renewed, config_secret_ref=config_reference(authority, sid, renewed, private)))


def _history_digest(conn, ids):
    import hashlib
    digest, count = hashlib.sha256(), 0
    placeholders = ','.join('?' for _ in ids)
    for row in conn.execute(f'SELECT * FROM messages WHERE session_id IN ({placeholders}) ORDER BY id', ids):
        encoded = json.dumps(tuple(row), ensure_ascii=False, separators=(',', ':'),
                             default=lambda value: {'sqlite_blob_hex': bytes(value).hex()})
        digest.update(encoded.encode())
        digest.update(b'\n')
        count += 1
    return count, digest.hexdigest()


def _config_digest(home):
    import hashlib
    return hashlib.sha256((home / 'config.yaml').read_bytes()).hexdigest()


def prepare_repair(db, home, *, session_id, old_home, cwd):
    """Read and validate a plan; *db* may be a protected snapshot for rehearsal only."""
    from types import SimpleNamespace
    from gateway.run import _load_gateway_config
    from gateway.session_policy import restore_policy
    from gateway.session_policy_credentials import recover_config_secrets
    from hermes_state_local import local_conversation_ids
    from hermes_state_mutation_prepared import local_snapshot
    from tui_gateway import git_probe

    fingerprint = _config_digest(home)
    config = _load_gateway_config(home / 'config.yaml')
    authority = SimpleNamespace(db=SimpleNamespace(db_path=home / 'state.db'), profile_id=str(home))
    with db._read_ctx() as conn:
        snapshot = local_snapshot(db, conn, session_id)
        ids = tuple(sorted(local_conversation_ids(conn, session_id)))
        history = _history_digest(conn, ids)
        row_cwds = [conn.execute('SELECT cwd FROM sessions WHERE id=?', (sid,)).fetchone()[0] for sid in ids]
    renewed = renew_policy(snapshot['receipt'], authority, config, old_home=old_home, cwd=cwd)
    # Use a fresh authority, not the renewal's in-memory private-value cache.
    cold = SimpleNamespace(db=SimpleNamespace(db_path=home / 'state.db'), profile_id=str(home))
    recover_config_secrets(cold, restore_policy(renewed), skip=())
    return {'snapshot': snapshot, 'policy': renewed, 'ids': ids, 'history': history, 'row_cwds': row_cwds,
            'config_digest': fingerprint,
            'git': {'branch': git_probe.branch(cwd), 'root': git_probe.common_repo_root(cwd)}}


def apply_prepared(db, home, plan, *, session_id, backup_path):
    """Apply under the caller's exclusive maintenance reservation, in one transaction.

    For a rehearsal, call only on an independent snapshot, never a live store.
    No profile/principal/legacy/lineage identity is created, changed or claimed.
    """
    from datetime import UTC, datetime
    import hashlib
    import uuid
    from hermes_state_local import POLICY_PREFIX
    from hermes_state_mutation_guards import require_idle
    from hermes_state_mutation_prepared import local_snapshot, workspace_in_transaction
    from hermes_state_runtime import RuntimeStoreError

    def write(conn):
        snapshot = local_snapshot(db, conn, session_id)
        if snapshot != plan['snapshot'] or _config_digest(home) != plan['config_digest']:
            raise RuntimeStoreError('revision_conflict')
        require_idle(db, conn, plan['ids'])
        if _history_digest(conn, plan['ids']) != plan['history']:
            raise RuntimeStoreError('revision_conflict')
        affected, result = workspace_in_transaction(db, conn, session_id, {}, plan)
        current = json.loads(conn.execute('SELECT value FROM state_meta WHERE key=?',
                                         (POLICY_PREFIX + session_id,)).fetchone()[0])
        old = snapshot['receipt']
        if any(current[k] != value for k, value in old.items() if k != 'policy'):
            raise RuntimeStoreError('identity_changed')
        if _history_digest(conn, plan['ids']) != plan['history']:
            raise RuntimeStoreError('history_changed')
        conn.executemany('UPDATE sessions SET runtime_revision=runtime_revision+1 WHERE id=?',
                         [(sid,) for sid in sorted(affected)])
        audit = {'kind': 'operator_credential_renewal', 'session_id': session_id,
                 'principal_id': f'uid:{os.getuid()}', 'at': datetime.now(UTC).isoformat(),
                 'old_policy_sha256': hashlib.sha256(json.dumps(old['policy'], sort_keys=True).encode()).hexdigest(),
                 'new_policy_sha256': hashlib.sha256(json.dumps(plan['policy'], sort_keys=True).encode()).hexdigest(),
                 'backup': str(backup_path), 'history_sha256': plan['history'][1], **result}
        conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)',
                     (f'maintenance.legacy_binding.v1:{session_id}:{uuid.uuid4().hex}', json.dumps(audit)))
        return result
    from gateway.runtime_ownership import exclusive_maintenance
    with exclusive_maintenance([Path(db.db_path).resolve().parent], purpose='legacy-binding-renewal'):
        return db._execute_write(write)


def _backup(db_path, home):
    import sqlite3
    import uuid
    directory = home / 'backups' / 'session-binding' / uuid.uuid4().hex
    directory.mkdir(mode=0o700, parents=True)
    destination = directory / 'state-before.db'
    os.close(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('backup_integrity_check_failed')
    finally:
        target.close()
        source.close()
    return destination


def _already_current(plan):
    return (json.dumps(plan['policy'], sort_keys=True) == json.dumps(plan['snapshot']['receipt']['policy'], sort_keys=True)
            and all(cwd == plan['policy']['cwd'] for cwd in plan['row_cwds']))


def _report(plan, session_id, *, applied, backup=None, result=None):
    return {'ok': True, 'applied': applied, 'session_id': session_id,
            'already_current': _already_current(plan),
            'old_cwd': plan['snapshot']['receipt']['policy']['cwd'], 'cwd': plan['policy']['cwd'],
            'history_messages': plan['history'][0], 'history_unchanged': True,
            'identity_unchanged': True, 'credential_source_verified': True,
            'requires_stopped_owner': True, 'backup': str(backup) if backup else None,
            **(result or {})}


def repair_home_binding(home, *, session_id, old_home, cwd, apply=False):
    """Preview by default. Apply only with the real profile owner lock reserved."""
    from gateway.runtime_ownership import exclusive_maintenance
    from hermes_state import SessionDB
    from hermes_state_holders import foreign_state_db_holders

    home = Path(home).resolve(strict=True)
    if apply:
        # Refuses a live Gateway, even this process's own runtime. No force option.
        with exclusive_maintenance([home], purpose='legacy-binding-renewal'):
            holders = foreign_state_db_holders(home / 'state.db')
            if holders:
                raise RuntimeError('foreign_database_holders')
            return _repair_stopped(home, session_id=session_id, old_home=old_home, cwd=cwd)
    db = SessionDB(db_path=home / 'state.db', read_only=True)
    try:
        plan = prepare_repair(db, home, session_id=session_id, old_home=old_home, cwd=cwd)
        return _report(plan, session_id, applied=False)
    finally:
        db.close()


def _repair_stopped(home, *, session_id, old_home, cwd):
    from hermes_state import SessionDB
    db = SessionDB(db_path=home / 'state.db', read_only=True)
    try:
        plan = prepare_repair(db, home, session_id=session_id, old_home=old_home, cwd=cwd)
    finally:
        db.close()
    if _already_current(plan):
        return _report(plan, session_id, applied=False)
    backup = _backup(home / 'state.db', home)
    db = SessionDB(db_path=home / 'state.db')
    try:
        result = apply_prepared(db, home, plan, session_id=session_id, backup_path=backup)
        # Independent readback, not just the return from the write transaction.
        from hermes_state_local import local_receipt
        if local_receipt(db, session_id)['policy'] != json.loads(json.dumps(plan['policy'])):
            raise RuntimeError('policy_readback_mismatch')
        return _report(plan, session_id, applied=True, backup=backup, result=result)
    finally:
        db.close()


def main(argv=None):
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', required=True)
    parser.add_argument('--old-home', required=True)
    parser.add_argument('--session-id', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--apply', action='store_true', help='Requires a drained and stopped Gateway')
    args = parser.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        report = repair_home_binding(args.home, session_id=args.session_id, old_home=args.old_home,
                                     cwd=args.cwd, apply=args.apply)
    except Exception as exc:
        # Config-parser errors can include source lines containing credentials. Never echo them.
        print(json.dumps({'ok': False, 'error': getattr(exc, 'reason', type(exc).__name__),
                          'field': getattr(exc, 'field', None)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
