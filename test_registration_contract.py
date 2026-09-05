"""Modern registration contract and shared-token safety; fake credentials only."""
import base64
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from test_plugin_safety import package, handlers, client as client_module

creds = importlib.import_module(f'{package.__name__}.hi_creds')


def registration(key=None):
    key = key or dict(v=1, id='test-client', secret='fake-secret')
    encoded = base64.urlsafe_b64encode(json.dumps(key).encode()).decode().rstrip('=')
    return dict(api_key='hi_ak_' + encoded, agent_id='test-agent', status='pending')


class RegistrationContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, XDG_CONFIG_HOME=self.temp.name)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_modern_registration_contract_and_no_duplicate(self):
        response = MagicMock()
        response.json.return_value = registration()
        with patch.object(creds.httpx, 'post', return_value=response) as post:
            first = creds.anonymous_register()
            second = creds.anonymous_register()
        self.assertEqual(first, second)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], 'https://hi.hirey.ai/v1/agents/api-keys')
        self.assertEqual(post.call_args.kwargs['json'], dict(agent_type='hermes', client_version='0.2.4', display_name=creds.ANON_REGISTER_DISPLAY_NAME))
        self.assertEqual(first['client_id'], 'test-client')
        self.assertNotIn('api_key', first)
        self.assertEqual(creds.credentials_path().stat().st_mode & 0o777, 0o600)
        self.assertFalse((creds.credentials_dir() / '.registration-pending.json').exists())

    def test_uncertain_registration_persists_marker_and_blocks_retry(self):
        with patch.object(creds.httpx, 'post', side_effect=RuntimeError('fake-secret')) as post:
            for _ in range(2):
                with self.assertRaises(RuntimeError) as error:
                    creds.anonymous_register()
                self.assertNotIn('fake-secret', str(error.exception))
        self.assertEqual(post.call_count, 1)
        self.assertFalse(creds.credentials_path().exists())
        marker = (creds.credentials_dir() / '.registration-pending.json').read_text()
        self.assertNotIn('secret', marker)

    def test_decoder_rejects_wrong_version_key_and_response_without_disclosure(self):
        bad = [registration(dict(v=True, id='id', secret='secret')),
               registration(dict(v=2, id='id', secret='secret')),
               registration(dict(v=1, id='', secret='secret')),
               dict(api_key='hi_ak_not+base64', agent_id='agent', status='pending'),
               dict(api_key='hi_ak_abc', agent_id='agent', status='active')]
        for body in bad:
            with self.assertRaises(ValueError) as error:
                creds.decode_registration(body, 'https://hi.hirey.ai')
            self.assertEqual(str(error.exception), 'Invalid Hi registration response')

    def test_referral_metadata_fails_before_side_effects(self):
        with patch.object(creds.httpx, 'post') as post:
            with self.assertRaises(ValueError):
                creds.anonymous_register(metadata={'channel_code': 'test-referral'})
        post.assert_not_called()
        self.assertFalse(creds.credentials_dir().exists())

    def test_refresh_rereads_rotated_token_under_shared_lock(self):
        old = creds.decode_registration(registration(), 'https://hi.hirey.ai')
        rotated = dict(old, access_token='rotated-fake-token', access_token_issued_at=4102444800,
                       access_token_expires_in=3600)
        creds.save(rotated)
        with patch.object(creds.httpx, 'post') as post:
            result = creds.refresh_token(old)
        post.assert_not_called()
        self.assertEqual(result, rotated)

    def test_pending_status_never_uses_active_me_endpoint(self):
        pending = creds.decode_registration(registration(), 'https://hi.hirey.ai')
        pending.update(access_token='hi_ai_fake-token', access_token_issued_at=4102444800, access_token_expires_in=3600)
        creds.save(pending)
        with patch.object(handlers, '_client') as client, patch.object(handlers.hi_client, 'HiClient') as api:
            client.return_value.plugin_policy.return_value = {}
            result = json.loads(handlers.handle_hi_agent_status({}))
        api.assert_not_called()
        self.assertFalse(result['identity_bound'])
        self.assertTrue(result['ready_for_public_reads'])

    def test_binding_verification_rotates_bearer_before_private_access(self):
        pending = creds.decode_registration(registration(), 'https://hi.hirey.ai')
        creds.save(pending)
        with patch.object(handlers, '_client') as client, patch.object(creds, '_refresh_token_locked') as refresh:
            client.return_value.call_capability.return_value = {'result': {'status': 'verified'}}
            handlers.build_capability_handler('hi.google-link')({'action': 'poll'})
        refresh.assert_called_once()
        self.assertEqual(refresh.call_args.args[0]['status'], 'active')

    def test_invalid_token_does_not_mutate_persisted_identity(self):
        pending = creds.decode_registration(registration(), 'https://hi.hirey.ai')
        creds.save(pending)
        response = MagicMock()
        response.json.return_value = {'access_token': 'fake-secret'}
        with patch.object(creds.httpx, 'post', return_value=response):
            with self.assertRaises(RuntimeError) as error:
                creds.refresh_token(pending)
        self.assertNotIn('fake-secret', str(error.exception))
        self.assertEqual(creds.load_strict(), pending)

    def test_flat_active_me_requires_matching_agent_and_person(self):
        active = creds.decode_registration(registration(), 'https://hi.hirey.ai')
        active.update(status='active', access_token='fake-token', access_token_issued_at=4102444800, access_token_expires_in=3600)
        creds.save(active)
        for agent_id, expected in [('test-agent', True), ('different-agent', False)]:
            with patch.object(handlers, '_client') as client, patch.object(handlers.hi_client, 'HiClient') as api:
                client.return_value.plugin_policy.return_value = {}
                api.return_value.get.return_value = dict(agent_id=agent_id, person_id='test-person')
                result = json.loads(handlers.handle_hi_agent_status({}))
            self.assertEqual(result['identity_bound'], expected)

    def test_historical_pending_status_does_not_hide_bound_jwt(self):
        active = creds.decode_registration(registration(), 'https://hi.hirey.ai')
        active.update(access_token='fake.jwt.token', access_token_issued_at=4102444800, access_token_expires_in=3600)
        creds.save(active)
        with patch.object(handlers, '_client') as client, patch.object(handlers.hi_client, 'HiClient') as api:
            client.return_value.plugin_policy.return_value = {}
            api.return_value.get.return_value = dict(agent_id='test-agent', person_id='test-person')
            result = json.loads(handlers.handle_hi_agent_status({}))
        self.assertTrue(result['identity_bound'])

    def test_all_http_methods_force_refresh_original_failed_bearer(self):
        for method in ('get', 'post', 'call_capability'):
            stored = creds.decode_registration(registration(), 'https://hi.hirey.ai')
            stored.update(access_token='hi_ai_failed', access_token_issued_at=4102444800, access_token_expires_in=3600)
            creds.save(stored)
            api = client_module.HiClient()
            api._http = MagicMock()
            bad, good = MagicMock(status_code=401), MagicMock(status_code=200)
            good.json.return_value = {'ok': True}
            getattr(api._http, 'get' if method == 'get' else 'post').side_effect = [bad, good]
            with patch.object(creds, 'refresh_token', return_value={**stored, 'access_token': 'hi_ai_new'}) as refresh:
                getattr(api, method)('/test') if method != 'call_capability' else api.call_capability('test', {})
            refresh.assert_called_once()
            self.assertTrue(refresh.call_args.kwargs['force'])
            self.assertEqual(refresh.call_args.args[0]['access_token'], 'hi_ai_failed')

    def test_401_environment_or_identity_change_never_sends_new_bearer(self):
        for change in ({'client_id': 'changed-client'}, {'platform_base_url': 'https://other.example'}):
            stored = creds.decode_registration(registration(), 'https://hi.hirey.ai')
            stored.update(access_token='hi_ai_failed', access_token_issued_at=4102444800, access_token_expires_in=3600)
            creds.save(stored)
            api = client_module.HiClient()
            api._http = MagicMock()
            def fail_request(*args, **kwargs):
                creds.save({**stored, **change})
                return MagicMock(status_code=401)
            api._http.get.side_effect = fail_request
            with patch.object(creds, 'refresh_token') as refresh:
                with self.assertRaises(client_module.HiAuthError):
                    api.get('/test')
            refresh.assert_not_called()
            self.assertEqual(api._http.get.call_count, 1)

    def test_insecure_remote_base_rejected_before_request(self):
        with patch.object(creds.httpx, 'post') as post:
            with self.assertRaises(ValueError):
                creds.anonymous_register(platform_base_url='http://remote.example')
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
