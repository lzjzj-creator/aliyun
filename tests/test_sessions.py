"""Offline v2 route, ownership, quota and retry tests. No live service calls."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

with patch.dict(os.environ, {
    'SUPABASE_URL': 'https://supabase.invalid',
    'SUPABASE_SERVICE_KEY': 'test-service-key',
    'KB_SOURCE_BASE': 'https://mirror.invalid',
}):
    from backend import memory, session_security as security
    from backend.routers.sessions import router
from tests.test_memory import Store, SID, OTHER

ANON = '11111111-1111-4111-8111-111111111111'
OTHER_ANON = '22222222-2222-4222-8222-222222222222'


class SessionRoutesTest(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.transport = httpx.Client(
            base_url='https://supabase.invalid/rest/v1',
            transport=httpx.MockTransport(self.store),
        )
        storage_patch = patch.object(memory, '_client', self.transport)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        self.addCleanup(self.transport.close)
        self.now = 0.0
        self.limiter = security.RateLimiter(clock=lambda: self.now)
        quota_patch = patch.object(security, 'limiter', self.limiter)
        quota_patch.start()
        self.addCleanup(quota_patch.stop)
        created = memory.create_session(ANON)
        self.headers = {'X-Anon-Id': ANON, 'X-Session-Token': created['token']}
        self.body = {'session_id': SID, 'client_msg_id': 'turn-one',
                     'user': 'I built a project', 'assistant': 'Tell me about your role'}
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def send(self, **changes):
        return self.client.post('/api/v2/messages', headers=self.headers,
                                json={**self.body, **changes})

    def test_metadata_list_excludes_sensitive_fields(self):
        self.send()
        response = self.client.get('/api/v2/sessions', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        items = response.json()['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(set(items[0]), {'id', 'title', 'stage'})
        for key in ('session_token_hash', 'token', 'messages', 'profile', 'content', 'anon_id'):
            self.assertNotIn(key, items[0])
        self.assertEqual(self.client.get('/api/v2/sessions',
                         headers={'X-Anon-Id': OTHER_ANON}).json(), {'items': []})

    def test_read_write_delete_uniform_ownership_failure(self):
        cases = [{}, {'X-Anon-Id': ANON},
                 {**self.headers, 'X-Session-Token': 'forged'},
                 {**self.headers, 'X-Anon-Id': OTHER_ANON},
                 {**self.headers, 'X-Anon-Id': 'not-a-uuid'}]
        for headers in cases:
            for method in ('GET', 'DELETE', 'POST'):
                with self.subTest(headers_present=tuple(headers), method=method):
                    path = '/api/v2/messages' if method == 'POST' else f'/api/v2/sessions/{SID}'
                    kwargs = {'json': self.body} if method == 'POST' else {}
                    response = self.client.request(method, path, headers=headers, **kwargs)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.json(), {'detail': '资源不存在'})
        self.assertEqual(self.store.messages, [])
        self.assertIn(SID, self.store.sessions)

    def test_malformed_and_unknown_session_return_404(self):
        for sid in ('not-a-uuid', OTHER):
            self.assertEqual(self.client.get(f'/api/v2/sessions/{sid}',
                             headers=self.headers).status_code, 404)
            self.assertEqual(self.send(session_id=sid).status_code, 404)

    def test_bundle_restore_and_delete_cascade(self):
        self.assertEqual(self.send().status_code, 200)
        bundle = self.client.get(f'/api/v2/sessions/{SID}', headers=self.headers).json()
        self.assertEqual(len(bundle['messages']), 2)
        self.assertNotIn('session_token_hash', bundle['session'])
        self.assertEqual(self.client.delete(f'/api/v2/sessions/{SID}',
                         headers=self.headers).status_code, 200)
        self.assertEqual(self.store.messages, [])
        self.assertEqual(self.store.profiles, {})
        self.assertEqual(self.client.get(f'/api/v2/sessions/{SID}',
                         headers=self.headers).status_code, 404)

    def test_retry_preserves_pair_and_charges_once(self):
        first = self.send()
        again = self.send()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), again.json())
        self.assertEqual(len(self.store.messages), 2)
        self.assertEqual(len(self.limiter._anon[ANON]), 1)

    def test_conflicting_retry_is_409_without_overwrite(self):
        self.send()
        for changes in ({'user': 'changed'}, {'assistant': 'changed'}):
            self.assertEqual(self.send(**changes).status_code, 409)
        self.assertEqual(self.store.messages[0]['content'], self.body['user'])
        self.assertEqual(self.store.messages[1]['content'], self.body['assistant'])
        self.assertEqual(len(self.store.messages), 2)

    def test_partial_failure_retry_completes_missing_assistant(self):
        original = memory.append_message
        def fail_assistant(sid, role, content, **kwargs):
            if role == 'assistant':
                raise memory.MemoryStoreError('private upstream detail')
            return original(sid, role, content, **kwargs)
        with patch.object(memory, 'append_message', side_effect=fail_assistant):
            response = self.send()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.text)
        self.assertEqual(len(self.store.messages), 1)
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(len(self.store.messages), 2)
        self.assertEqual(len(self.limiter._anon[ANON]), 1)
        self.assertFalse(self.limiter._streams)

    def test_ip_limit_and_expiration(self):
        for _ in range(20):
            self.assertEqual(self.client.get('/api/v2/sessions', headers=self.headers).status_code, 200)
        response = self.client.get('/api/v2/sessions', headers=self.headers)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '60')
        self.now = 60
        self.assertEqual(self.client.get('/api/v2/sessions', headers=self.headers).status_code, 200)

    def test_daily_turn_limit_and_expiration(self):
        for _ in range(100):
            self.assertTrue(self.limiter.check_anon(ANON)[0])
        response = self.send()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '86400')
        self.assertEqual(self.store.messages, [])
        self.now = 86400
        self.assertEqual(self.send().status_code, 200)

    def test_active_session_lease_blocks_writes_and_deletes(self):
        with self.limiter.stream(SID):
            for response in (self.send(), self.client.delete(f'/api/v2/sessions/{SID}',
                                                           headers=self.headers)):
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.headers['Retry-After'], '1')
        self.assertEqual(self.send().status_code, 200)

    def test_concurrent_pair_does_not_duplicate(self):
        entered, release = threading.Event(), threading.Event()
        original = memory.append_message
        def blocked(sid, role, content, **kwargs):
            if role == 'user':
                entered.set()
                if not release.wait(5):
                    raise AssertionError('test synchronization timed out')
            return original(sid, role, content, **kwargs)
        with patch.object(memory, 'append_message', side_effect=blocked):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.send)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(self.send().status_code, 429)
                finally:
                    release.set()
                self.assertEqual(first.result(timeout=5).status_code, 200)
        self.assertEqual(self.send().status_code, 200)
        self.assertEqual(len(self.store.messages), 2)

    def test_storage_errors_sanitized_including_auth(self):
        for target in ('verify_session', 'get_session_bundle', 'list_sessions', 'delete_session'):
            with self.subTest(target=target):
                with patch.object(memory, target, side_effect=memory.MemoryStoreError('secret-token body')):
                    if target == 'list_sessions':
                        response = self.client.get('/api/v2/sessions', headers=self.headers)
                    elif target == 'delete_session':
                        response = self.client.delete(f'/api/v2/sessions/{SID}', headers=self.headers)
                    else:
                        response = self.client.get(f'/api/v2/sessions/{SID}', headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn('secret', response.text)
        self.assertFalse(self.limiter._streams)

    def test_input_limits_and_untrusted_fields_rejected(self):
        for changes in ({'client_msg_id': ' '}, {'user': ' '}, {'assistant': ''},
                        {'user': 'x' * 12001}, {'assistant': 'x' * 30001},
                        {'role': 'system'}, {'meta': {'x': 'y' * 4001}}):
            with self.subTest(fields=tuple(changes)):
                self.assertEqual(self.send(**changes).status_code, 422)
        self.assertEqual(self.store.messages, [])

    def test_null_session_id_creates_session_returns_token_once(self):
        # fixture messages.json 契约：首轮 session_id=null → 服务端建会话
        meta = {'route': {'intent': 'kb_qa'}, 'sources': [{'title': 't', 'url': 'https://m/x'}]}
        response = self.client.post('/api/v2/messages', headers={'X-Anon-Id': ANON},
                                    json={k: v for k, v in {**self.body, 'meta': meta}.items()
                                          if k != 'session_id'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['session_id'], SID)
        self.assertTrue(data['session_token'])
        self.assertEqual(data['message_ids'], [1, 2])
        self.assertEqual(len(self.store.messages), 2)
        self.assertEqual(self.store.messages[1]['meta'], meta)
        self.assertEqual(len(self.limiter._anon[ANON]), 1)
        # 重试必须复用 session_id + token（幂等返回原 pair，不再发令牌/计费）
        headers = {'X-Anon-Id': ANON, 'X-Session-Token': data['session_token']}
        retry = self.client.post('/api/v2/messages', headers=headers, json=self.body)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), {'session_id': SID, 'message_ids': [1, 2]})
        self.assertNotIn('session_token', retry.json())
        self.assertEqual(len(self.store.messages), 2)
        self.assertEqual(len(self.limiter._anon[ANON]), 1)
        no_token = self.client.post('/api/v2/messages', headers={'X-Anon-Id': ANON}, json=self.body)
        self.assertEqual(no_token.status_code, 404)

    def test_canonical_ids_share_ownership_and_lease(self):
        self.headers['X-Anon-Id'] = ANON.replace('-', '')
        self.assertEqual(self.send(session_id=SID.upper()).status_code, 200)
        with self.limiter.stream(SID.upper()):
            self.assertEqual(self.send().status_code, 429)


class LimiterTest(unittest.TestCase):
    def test_capacity_fails_closed_then_reclaims_expired(self):
        now = [0.0]
        limiter = security.RateLimiter(clock=lambda: now[0], capacity=2)
        self.assertTrue(limiter.check_ip('a')[0])
        self.assertTrue(limiter.check_ip('b')[0])
        self.assertFalse(limiter.check_ip('c')[0])
        self.assertEqual(len(limiter._ip), 2)
        now[0] = 60
        self.assertTrue(limiter.check_ip('c')[0])
        self.assertEqual(len(limiter._ip), 1)

    def test_lease_released_after_exception_and_capacity_bounded(self):
        limiter = security.RateLimiter(capacity=1)
        with self.assertRaises(RuntimeError):
            with limiter.stream(SID):
                raise RuntimeError('cancelled')
        self.assertFalse(limiter._streams)
        with limiter.stream(SID):
            with self.assertRaises(HTTPException) as caught:
                with limiter.stream(OTHER):
                    pass
            self.assertEqual(caught.exception.status_code, 429)
        self.assertFalse(limiter._streams)

    def test_retry_after_rounds_up(self):
        now = [0.0]
        limiter = security.RateLimiter(clock=lambda: now[0])
        for _ in range(20):
            limiter.check_ip('a')
        now[0] = 0.1
        self.assertEqual(limiter.check_ip('a'), (False, 60))

    def test_last_forwarded_hop_and_fallback(self):
        from starlette.requests import Request
        req = Request({'type': 'http', 'headers': [(b'x-forwarded-for', b'1.2.3.4, 5.6.7.8')],
                       'client': ('10.0.0.1', 1)})
        self.assertEqual(security.client_ip(req), '5.6.7.8')
        req = Request({'type': 'http', 'headers': [], 'client': ('10.0.0.1', 1)})
        self.assertEqual(security.client_ip(req), '10.0.0.1')


if __name__ == '__main__':
    unittest.main()
