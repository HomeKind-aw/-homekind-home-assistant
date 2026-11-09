import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from homekind_maintenance.recovery_ui import RecoveryServer,recovery_page

class RecoveryUITests(unittest.TestCase):
    def test_local_key_is_only_shown_after_selecting_the_recorded_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'system-backups').mkdir();ident='74000000-0000-4000-8000-000000000001'
            (root/'system-backups'/(ident+'.json')).write_text(json.dumps({'id':ident,'backup_id':'abcd1234','password':'a'*64,'component':'core','before':'2026.10.4'}))
            self.assertNotIn('a'*64,recovery_page(root));self.assertIn('a'*64,recovery_page(root,ident))
            self.assertNotIn('a'*64,recovery_page(root,'../maintenance.json'))
    def test_direct_network_access_is_rejected_even_with_forged_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            server=RecoveryServer(('127.0.0.1',0),Path(tmp))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                connection=http.client.HTTPConnection(*server.server_address)
                connection.request('GET','/',headers={'X-Forwarded-For':'172.30.32.2'})
                response=connection.getresponse();self.assertEqual(response.status,403)
                self.assertEqual(response.getheader('Cache-Control'),'no-store');connection.close()
            finally:server.shutdown();server.server_close()
