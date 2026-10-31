"""Cron footer behavior exercised through actual run and delivery queue."""
from tests.cron.test_run_one_job import _patch_pipeline
from cron import scheduler


def test_agent_queue_has_measured_footer(monkeypatch):
    queued = []
    _patch_pipeline(monkeypatch, final='真实正文\n⚡ 执行耗时：999 秒\n🕘 上报时间：旧')
    monkeypatch.setattr('cron.delivery_queue.enqueue', lambda eid, job, content, **kw: queued.append(content) or {'status': 'pending'})
    assert scheduler.run_one_job({'id': 'local-footer-test', 'name': 'fixture', 'deliver': 'telegram'})
    assert len(queued) == 1
    assert '真实正文\n\n⚡ 执行耗时：' in queued[0]
    assert '999 秒' not in queued[0]
    assert queued[0].count('⚡ 执行耗时') == 1


def test_silent_agent_not_enqueued(monkeypatch):
    queued = []
    _patch_pipeline(monkeypatch, final='[SILENT]')
    monkeypatch.setattr('cron.delivery_queue.enqueue', lambda *a, **kw: queued.append(a))
    assert scheduler.run_one_job({'id': 'local-footer-silent', 'name': 'fixture', 'deliver': 'telegram'})
    assert queued == []
