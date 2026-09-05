"""Execute the real installer against isolated fake commands; never reach production."""
import json
import base64
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class InstallerSafety(unittest.TestCase):
    def run_install(self, contents=None, response='{}', locked=False, retry=False, channel='', base=None):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            creds = root / 'config/hi/credentials.json'
            creds.parent.mkdir(parents=True)
            if locked:
                (creds.parent / '.register.lock').mkdir()
            if contents is not None:
                creds.write_text(contents)
            plugin = root / 'hermes/plugins/hirey-hi'
            plugin.mkdir(parents=True)
            (plugin / 'plugin.yaml').write_text('version: 0.2.4\n')
            commands = {
                'hermes': '#!/bin/sh\nexit 0\n',
                'curl': '#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_CALLS"\ncase "$*" in *oauth/token*) printf "%s" "${TEST_TOKEN_RESPONSE:-$TEST_RESPONSE}" ;; *) printf "%s" "$TEST_RESPONSE" ;; esac\n',
                # A held registration lock must time out without actually sleeping.
                'sleep': '#!/bin/sh\nexit 0\n',
            }
            for name, code in commands.items():
                path = bin_dir / name
                path.write_text(code)
                path.chmod(0o700)
            env = dict(os.environ, PATH=f'{bin_dir}:/usr/bin:/bin:/opt/homebrew/bin',
                       HERMES_HOME=str(root / 'hermes'), XDG_CONFIG_HOME=str(root / 'config'),
                       TEST_CALLS=str(root / 'calls'), TEST_RESPONSE=response, HI_CHANNEL_CODE=channel)
            if 'api_key' in response:
                env['TEST_TOKEN_RESPONSE'] = '{"access_token":"fake-token","expires_in":3600}'
            env.pop('HI_BASE', None)
            if base is not None:
                env['HI_BASE'] = base
            result = subprocess.run(['bash', str(Path(__file__).with_name('install.sh'))],
                                    env=env, capture_output=True, text=True)
            if retry:
                result = subprocess.run(['bash', str(Path(__file__).with_name('install.sh'))],
                                        env=env, capture_output=True, text=True)
            self.calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
            self.marker = (creds.parent / '.registration-pending.json').exists()
            return result, creds.read_text() if creds.exists() else None, (root / 'calls').exists()

    def test_corrupt_existing_identity_never_registers_or_changes_file(self):
        for contents in ('{bad', '{}', '{"client_id":"existing"}', '[]'):
            result, saved, called = self.run_install(contents)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(saved, contents)
            self.assertFalse(called)

    def test_invalid_registration_response_is_never_saved(self):
        result, saved, called = self.run_install(response='{"auth":{"client_id":"incomplete"}}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(saved)
        self.assertTrue(called)

    def test_busy_shared_lock_never_registers(self):
        result, saved, called = self.run_install(locked=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(saved)
        self.assertFalse(called)

    def test_uncertain_registration_retry_does_not_post_twice(self):
        result, saved, _ = self.run_install(response='{}', retry=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(saved)
        self.assertTrue(self.marker)
        self.assertEqual(self.calls.count('/v1/agents/api-keys'), 1)

    def test_modern_contract_success_saves_shared_credentials(self):
        key = base64.urlsafe_b64encode(json.dumps(dict(v=1, id='test-client', secret='test-secret')).encode()).decode().rstrip('=')
        response = json.dumps(dict(api_key='hi_ak_' + key, agent_id='test-agent', status='pending'))
        result, saved, _ = self.run_install(response=response)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(saved)['client_id'], 'test-client')
        self.assertEqual(json.loads(saved)['status'], 'pending')
        self.assertNotIn('api_key', json.loads(saved))
        self.assertFalse(self.marker)
        self.assertIn('"agent_type":"hermes"', self.calls)
        self.assertNotIn('/v1/agents/register', self.calls)

    def test_referral_is_rejected_before_registration(self):
        result, saved, called = self.run_install(channel='test-referral')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(saved)
        self.assertFalse(called)
        self.assertFalse(self.marker)

    def test_invalid_token_keeps_existing_identity_and_does_not_echo_secrets(self):
        contents = json.dumps(dict(client_id='existing', client_secret='fake-secret', audience='hirey-hi'))
        result, saved, called = self.run_install(contents, '{"access_token":"do-not-echo"}')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(saved, contents)
        self.assertTrue(called)
        self.assertNotIn('do-not-echo', result.stdout + result.stderr)

    def test_fresh_existing_identity_needs_no_network(self):
        contents = json.dumps(dict(client_id='existing', client_secret='fake-secret', audience='hirey-hi',
                                   agent_id='test-agent', access_token='fake-token',
                                   access_token_issued_at=4102444800, access_token_expires_in=3600))
        result, saved, called = self.run_install(contents)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(saved, contents)
        self.assertFalse(called)

    def test_stored_base_is_preserved_and_explicit_conflict_rejected(self):
        contents = json.dumps(dict(client_id='existing', client_secret='fake-secret', audience='hirey-hi',
                                   platform_base_url='https://test.example'))
        self.run_install(contents, response='{"access_token":"fake-token","expires_in":3600}')
        self.assertIn('https://test.example/oauth/token', self.calls)
        result, saved, called = self.run_install(contents, base='https://hi.hirey.ai')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(saved, contents)
        self.assertFalse(called)

    def test_remote_plain_http_rejected(self):
        result, saved, called = self.run_install(base='http://remote.example')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(saved)
        self.assertFalse(called)


if __name__ == '__main__':
    unittest.main()
