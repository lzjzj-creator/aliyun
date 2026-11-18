"""Offline application/FC import and opt-in CORS integration checks."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = '''
import json
import sys
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, 'backend')
with patch.object(httpx.HTTPTransport, 'handle_request', side_effect=AssertionError('Live HTTP forbidden')):
    import main
    paths = main.app.openapi()['paths']
    with TestClient(main.app) as client:
        health = client.get('/health')
        cors = client.options('/api/v2/sessions/example', headers={
            'Origin': 'http://localhost:5173',
            'Access-Control-Request-Method': 'DELETE',
            'Access-Control-Request-Headers': 'X-Anon-Id, X-Session-Token',
        })
        missing = client.get('/api/v2/sessions')
    print(json.dumps({
        'health': health.status_code,
        'v1_chat': '/api/chat/stream' in paths,
        'sessions': '/api/v2/sessions' in paths,
        'messages': '/api/v2/messages' in paths,
        'cors': cors.status_code,
        'methods': cors.headers.get('access-control-allow-methods', ''),
        'headers': cors.headers.get('access-control-allow-headers', '').lower(),
        'exposed': next(m.kwargs.get('expose_headers', []) for m in main.app.user_middleware
                        if m.cls.__name__ == 'CORSMiddleware'),
        'missing': missing.status_code,
    }))
'''


class ApplicationIntegrationTest(unittest.TestCase):
    def inspect(self, enabled):
        env = {**os.environ,
               'SUPABASE_URL': 'https://supabase.invalid',
               'SUPABASE_SERVICE_KEY': 'test-only',
               'DASHSCOPE_API_KEY': 'test-only',
               'DEEPSEEK_API_KEY': '',
               'ALLOWED_ORIGINS': 'http://localhost:5173',
               'V2_MEMORY_ENABLED': enabled,
               'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONIOENCODING': 'utf-8'}
        result = subprocess.run([sys.executable, '-B', '-c', PROGRAM], cwd=ROOT,
                                env=env, capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['health'], 200)
        self.assertTrue(data['v1_chat'])
        self.assertEqual(data['cors'], 200)
        self.assertIn('DELETE', data['methods'])
        self.assertIn('x-anon-id', data['headers'])
        self.assertIn('x-session-token', data['headers'])
        self.assertIn('Retry-After', data['exposed'])
        self.assertEqual(data['missing'], 404)
        return data

    def test_v2_disabled_preserves_v1_application(self):
        data = self.inspect('0')
        self.assertFalse(data['sessions'])
        self.assertFalse(data['messages'])

    def test_v2_enabled_mounts_fc_style_imports_and_routes(self):
        data = self.inspect('1')
        self.assertTrue(data['sessions'])
        self.assertTrue(data['messages'])


if __name__ == '__main__':
    unittest.main()
