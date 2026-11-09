"""Backend-aware lifecycle reads; no commands in these fixtures are executed."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.terminal_tool_guards import gateway_lifecycle_block


class BackendReads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'], prefix='guard-backend-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def scan(self, command, backend='local', remote_source=None):
        calls = []

        def execute(request):
            calls.append(request)
            if remote_source is not None and len(calls) == 1:
                return {'returncode': 0, 'output': remote_source}
            return {'returncode': 1, 'output': ''}

        env = SimpleNamespace(execute=execute, cwd=str(self.root))
        registry = SimpleNamespace(_is_supervised_gateway_process=lambda: True)
        terminal = SimpleNamespace(get_session_cwd=lambda key: str(self.root),
                                   _resolve_command_cwd=lambda **kwargs: str(self.root))
        with patch.dict('sys.modules', {'tools.process_registry': registry, 'tools.terminal_tool': terminal}):
            result = gateway_lifecycle_block(command=command, env=env, env_type=backend,
                cwd=str(self.root), workdir=None, session_key='unit-backend')
        return result, calls

    def source(self):
        return '#!/usr/bin/python3\n' + ''.join(
            f'def report_{i}():\n    """Return report /examples/{i}."""\n    pass\n'
            for i in range(80))

    def test_local_filesystem_misses_never_spawn_backend_reads(self):
        script = self.root / 'tool'
        script.write_text(self.source())
        result, calls = self.scan(str(script))
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_local_referenced_lifecycle_command_remains_blocked(self):
        child = self.root / 'child.sh'
        child.write_text('hermes gateway restart\n')
        outer = self.root / 'outer.sh'
        outer.write_text('bash ' + str(child) + '\n')
        result, calls = self.scan(str(outer))
        self.assertIsNotNone(result)
        self.assertEqual(calls, [])

    def test_local_oversized_script_remains_fail_closed(self):
        script = self.root / 'large.sh'
        script.write_text('#' * (1024 * 1024 + 1))
        result, calls = self.scan(str(script))
        self.assertIn('could not scan', result or '')
        self.assertEqual(calls, [])

    def test_dynamic_docstring_cloud_path_remains_fail_closed(self):
        cloud = self.root / 'Library/Mobile Documents/child.sh'
        script = self.root / 'tool'
        script.write_text('import os\ndef f():\n    """' + str(cloud) + '"""\n    pass\n'
            'os.system(getattr(f, chr(95)*2 + "doc" + chr(95)*2))\n')
        result, calls = self.scan(str(script))
        self.assertIn('cloud-synced', result or '')
        self.assertEqual(calls, [])

    def test_remote_backend_still_reads_remote_scripts(self):
        result, calls = self.scan('/remote/fixture-tool', 'ssh', 'echo okay\n')
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)

    def test_remote_lifecycle_command_remains_blocked(self):
        result, calls = self.scan('/remote/fixture-tool', 'ssh', 'hermes gateway restart\n')
        self.assertIsNotNone(result)
        self.assertEqual(len(calls), 1)

    def test_remote_read_budget_is_not_relaxed(self):
        result, calls = self.scan('/remote/fixture-tool', 'ssh', self.source())
        self.assertIn('remote reads', result or '')
        self.assertEqual(len(calls), 64)


if __name__ == '__main__':
    unittest.main()
