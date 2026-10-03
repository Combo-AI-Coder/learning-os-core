"""Synthetic REST text admission; no network, credentials or learner state."""
import base64
import unittest
from unittest import mock
from scripts import runtime_adapter as adapter

RID = 9000000401
HEAD = 'a' * 40
BLOB = 'b' * 40

class RestTextTests(unittest.TestCase):
    def provider(self, raw=b'hello', **changes):
        p = adapter.GitHubApiProvider(token='synthetic-unused')
        self.addCleanup(p.close)
        p._repo = mock.Mock(return_value={'id': RID, 'full_name': 'synthetic/repo'})
        p._commit = mock.Mock(return_value=HEAD)
        data = {'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(raw).decode(), 'sha': BLOB}
        data.update(changes)
        p._request = mock.Mock(return_value=data)
        return p

    def test_unsafe_path_is_rejected_before_any_network_call(self):
        for path in ('../state', 'a/../state', '.git/config', 'CON', 'a\\b', '/absolute', 'a//b', ''):
            with self.subTest(path=path):
                p = self.provider()
                with self.assertRaises(adapter.ResolutionError):
                    p.read_text(RID, 'main', path)
                p._repo.assert_not_called()
                p._request.assert_not_called()

    def test_decoded_text_limit_is_independent_of_json_envelope(self):
        p = self.provider(b'x' * 9)
        with mock.patch.object(adapter, 'MAX_TEXT_BLOB_BYTES', 8):
            with self.assertRaises(adapter.ResolutionError):
                p.read_text(RID, 'main', 'state.txt')

    def test_base64_junk_is_not_silently_discarded(self):
        for value in ('a!!GVsbG8=', 'aGVsbG8=garbage', 'aGVsbG8=\u00a0'):
            with self.subTest(value=value):
                p = self.provider(content=value)
                with self.assertRaises(adapter.ResolutionError):
                    p.read_text(RID, 'main', 'state.txt')

    def test_blob_identity_must_be_an_exact_sha(self):
        for value in ('short', 'g' * 40, True, 123, None):
            with self.subTest(value=value):
                p = self.provider(sha=value)
                with self.assertRaises(adapter.ResolutionError):
                    p.read_text(RID, 'main', 'state.txt')

    def test_non_file_response_is_not_read_as_text(self):
        for kind in ('dir', 'symlink', 'submodule', None):
            with self.subTest(kind=kind):
                p = self.provider(type=kind)
                with self.assertRaises(adapter.ResolutionError):
                    p.read_text(RID, 'main', 'state.txt')

    def test_api_line_wrapping_empty_files_and_exact_limit_remain_valid(self):
        for raw in (b'', b'12345678', 'hello \u6d4b\u8bd5'.encode('utf-8')):
            p = self.provider(raw, content=base64.encodebytes(raw).decode().replace('\n', '\r\n'))
            with mock.patch.object(adapter, 'MAX_TEXT_BLOB_BYTES', max(8, len(raw))):
                self.assertEqual(p.read_text(RID, 'main', 'docs/state.txt'), (raw.decode(), BLOB, HEAD))
        self.assertIn('?ref=' + HEAD, p._request.call_args.args[1])

    def test_invalid_utf8_and_nonstring_content_fail_closed(self):
        for change in ({'content': None}, {'content': 12}, {'content': '//4='}):
            with self.subTest(change=change):
                p = self.provider(**change)
                with self.assertRaises(adapter.ResolutionError):
                    p.read_text(RID, 'main', 'state.txt')
