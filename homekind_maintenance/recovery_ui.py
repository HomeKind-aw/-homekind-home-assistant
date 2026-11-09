"""Local Home Assistant Ingress view of recorded recovery keys.

No cloud credentials, generic filesystem routes or restore operations are exposed.
Only the authenticated Supervisor Ingress peer may read this page.
"""
import html
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs


def recovery_page(directory, selected=None):
    entries = []
    folder = directory / 'system-backups'
    for path in sorted(folder.glob('*.json'), reverse=True)[:50]:
        try:
            if path.stat().st_size > 16384:
                continue
            value = json.loads(path.read_text())
            if not re.fullmatch(r'[0-9a-f-]{36}', value.get('id', '')) or path.stem != value['id'] or not re.fullmatch(r'[a-z0-9]{8,64}', value.get('backup_id', '')) or not re.fullmatch(r'[0-9a-f]{64}', value.get('password', '')):
                continue
            entries.append(value)
        except (OSError, ValueError, TypeError, KeyError):
            continue
    escape = lambda value: html.escape(str(value), quote=True)
    rows = []
    for value in entries:
        key = ('<label>Backup password <input readonly autocomplete="off" value="' + escape(value['password']) + '"></label>') if selected == value['id'] else ('<a href="?backup=' + escape(value['id']) + '">Show recovery key</a>')
        rows.append('<article><h2>' + escape(value.get('component', 'Home Assistant')) + ' · ' + escape(value.get('before', '')) + '</h2><p>Backup reference: <code>' + escape(value['backup_id']) + '</code></p>' + key + '</article>')
    return '''<!doctype html><html lang="en-AU"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>HomeKind backup recovery</title><style>body{font:18px system-ui;background:#f8f6ef;color:#183b63;max-width:48rem;margin:2rem auto;padding:1rem}article{background:white;border-radius:1rem;padding:1rem;margin:1rem 0}input{box-sizing:border-box;width:100%;font:16px monospace;padding:.8rem}h1{font-size:1.8rem}p{line-height:1.5}a{color:#183b63}</style><h1>HomeKind backup recovery</h1><p>Each scheduled system update keeps an encrypted full backup, including the Home Assistant database. Keep its password somewhere safe with a downloaded copy of the backup.</p><p>For recovery, open Home Assistant → Settings → System → Backups, select the matching backup and use its password. An installer should review a restore because it returns the whole home to that saved point.</p>''' + (''.join(rows) or '<p>No system-update backup has been recorded yet.</p>') + '</html>'


class RecoveryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory, ingress_ip='172.30.32.2'):
        self.directory, self.ingress_ip = directory, ingress_ip
        super().__init__(address, RecoveryHandler)


class RecoveryHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        allowed = self.client_address[0] == self.server.ingress_ip
        if not allowed or parsed.path != '/':
            body, code = b'Open this page inside Home Assistant.', 403
        else:
            selected = parse_qs(parsed.query).get('backup', [None])[0]
            body, code = recovery_page(self.server.directory, selected).encode(), 200
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)


def start_recovery_ui(companion):
    server = RecoveryServer(('0.0.0.0', 8101), companion.directory)
    threading.Thread(target=server.serve_forever, name='homekind-local-recovery', daemon=True).start()
    return server
