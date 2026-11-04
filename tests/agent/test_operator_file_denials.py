"""Operator file denies supplement, never weaken, native safety rules."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.file_safety import get_read_block_error, is_write_denied


class OperatorFileDenials(unittest.TestCase):
    def test_configured_directory_denies_reads_inside_allowed_home(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            private = Path(root) / 'private'
            private.mkdir()
            token = private / 'config.json'
            token.write_text('{"fixture": "original"}\n')
            with patch.dict(os.environ, {'HERMES_FILE_DENY_ROOTS': str(private),
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                self.assertIsNotNone(get_read_block_error(str(token)))

    def test_configured_file_denies_writes_inside_allowed_home(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            token = Path(root) / 'auth.json'
            token.write_text('{"fixture": "original"}\n')
            with patch.dict(os.environ, {'HERMES_FILE_DENY_ROOTS': str(token),
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                self.assertTrue(is_write_denied(str(token)))

    def test_filesystem_root_denial_includes_descendants(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            token = Path(root) / 'ordinary.json'
            with patch.dict(os.environ, {'HERMES_FILE_DENY_ROOTS': os.path.abspath(os.sep),
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                self.assertTrue(is_write_denied(str(token)))

    def test_unconfigured_policy_preserves_native_behaviour(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            with patch.dict(os.environ, {'HERMES_FILE_DENY_ROOTS': '',
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                ordinary = str(Path(root) / 'ordinary.json')
                self.assertIsNone(get_read_block_error(ordinary))
                self.assertFalse(is_write_denied(ordinary))
                self.assertIsNotNone(get_read_block_error(str(Path(root) / '.env')))

    def test_exact_file_root_does_not_block_sibling_prefix(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            token = Path(root) / 'token.json'
            sibling = Path(root) / 'token.json-example'
            with patch.dict(os.environ, {'HERMES_FILE_DENY_ROOTS': str(token),
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                self.assertIsNotNone(get_read_block_error(str(token)))
                self.assertTrue(is_write_denied(str(token)))
                self.assertIsNone(get_read_block_error(str(sibling)))
                self.assertFalse(is_write_denied(str(sibling)))

    def test_symlink_cannot_bypass_denied_target(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            private = Path(root) / 'private'
            private.mkdir()
            target = private / 'token.json'
            target.write_text('{}\n')
            link = Path(root) / 'ordinary.json'
            link.symlink_to(target)
            with patch.dict(os.environ, {'HERMES_FILE_DENY_ROOTS': str(private),
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                self.assertIsNotNone(get_read_block_error(str(link)))
                self.assertTrue(is_write_denied(str(link)))

    def test_multiple_roots_and_tilde_expansion(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            roots = os.pathsep.join(['~/private-one', str(Path(root) / 'private-two')])
            with patch.dict(os.environ, {'HOME': root, 'HERMES_FILE_DENY_ROOTS': roots,
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                for name in ['private-one', 'private-two']:
                    target = str(Path(root) / name / 'token.json')
                    self.assertIsNotNone(get_read_block_error(target))
                    self.assertTrue(is_write_denied(target))

    def test_operator_denial_precedes_ssh_config_approval(self):
        with tempfile.TemporaryDirectory(dir=os.environ['HOME']) as root:
            target = Path(root) / '.ssh/config'
            with patch.dict(os.environ, {'HOME': root, 'HERMES_FILE_DENY_ROOTS': str(target),
                                         'HERMES_WRITE_SAFE_ROOT': root}):
                self.assertTrue(is_write_denied(str(target)))


if __name__ == '__main__':
    unittest.main()
