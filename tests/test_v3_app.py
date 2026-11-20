"""#89 dual-state smoke: V3_MEMORY_ENABLED mount gating + CORS contract."""
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
        memory = None
        if __import__('os').getenv('V3_MEMORY_ENABLED') == '0':
            memory = client.get('/api/v3/memory',
                                headers={'X-Owner-Token': 'x' * 32}).status_code
        cors = client.options('/api/v3/memory/export', headers={
            'Origin': 'http://localhost:5173',
            'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'X-Owner-Token',
        })
        v2 = client.get('/api/v2/sessions')
    print(json.dumps({
        'memory': memory,
        'memory_path': '/api/v3/memory' in paths,
        'facts_path': '/api/v3/facts' in paths,
        'resumes_path': '/api/v3/resumes' in paths,
        'forget_path': '/api/v3/memory/sessions/{session_id}' in paths,
        'versions_path': '/api/v3/resumes/{doc_id}/versions' in paths,
        'batch_path': '/api/v3/resumes/batch' in paths,
        'cors': cors.status_code,
        'cors_headers': cors.headers.get('access-control-allow-headers', '').lower(),
        'v2_status': v2.status_code,
    }))
'''

V3_PATHS = ('memory_path', 'facts_path', 'resumes_path',
            'forget_path', 'versions_path', 'batch_path')


class V3FlagSmokeTest(unittest.TestCase):
    def inspect(self, enabled):
        env = {**os.environ,
               'SUPABASE_URL': 'https://supabase.invalid',
               'SUPABASE_SERVICE_KEY': 'test-only',
               'DASHSCOPE_API_KEY': 'test-only',
               'DEEPSEEK_API_KEY': '',
               'ALLOWED_ORIGINS': 'http://localhost:5173',
               'V3_MEMORY_ENABLED': enabled,
               'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONIOENCODING': 'utf-8'}
        result = subprocess.run([sys.executable, '-B', '-c', PROGRAM], cwd=ROOT,
                                env=env, capture_output=True, encoding='utf-8',
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_disabled_mounts_nothing(self):
        data = self.inspect('0')
        for key in V3_PATHS:
            self.assertFalse(data[key], key)
        self.assertEqual(data['memory'], 404)

    def test_enabled_mounts_all_and_cors_allows_owner_token(self):
        data = self.inspect('1')
        for key in V3_PATHS:
            self.assertTrue(data[key], key)
        # X-Owner-Token must survive browser preflight (found missing in #89)
        self.assertEqual(data['cors'], 200)
        self.assertIn('x-owner-token', data['cors_headers'])


if __name__ == '__main__':
    unittest.main()
