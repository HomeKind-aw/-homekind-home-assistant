"""Local Home Assistant Ingress view of recorded recovery keys.

No cloud credentials, generic filesystem routes or restore operations are exposed.
Only the authenticated Supervisor Ingress peer may read this page.
"""
import html
import json
import re
import threading
import secrets
import hmac
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
    return '''<!doctype html><html lang="en-AU"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Belmelo backup recovery</title><style>body{font:18px system-ui;background:#f8f6ef;color:#183b63;max-width:48rem;margin:2rem auto;padding:1rem}article{background:white;border-radius:1rem;padding:1rem;margin:1rem 0}input{box-sizing:border-box;width:100%;font:16px monospace;padding:.8rem}h1{font-size:1.8rem}p{line-height:1.5}a{color:#183b63}</style><h1>Belmelo backup recovery</h1><p>Each scheduled system update keeps an encrypted full backup, including the Home Assistant database. Keep its password somewhere safe with a downloaded copy of the backup.</p><p>For recovery, open Home Assistant → Settings → System → Backups, select the matching backup and use its password. An installer should review a restore because it returns the whole home to that saved point.</p>''' + (''.join(rows) or '<p>No system-update backup has been recorded yet.</p>') + '</html>'


def maintenance_page(companion, csrf, selected=None, error=None):
    status = companion.setup_status()
    paired, connected = status['paired'], status['connected']
    escape = lambda value: html.escape(str(value), quote=True)
    form = '<form method="post" action="pair"><input type="hidden" name="csrf" value="' + escape(csrf) + '"><label for="pairing-code">Maintenance code from Belmelo</label><textarea id="pairing-code" name="pairing_code" rows="3" required maxlength="110" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="Paste your code here"></textarea><label class="choice"><input type="checkbox" name="allow_maintenance" value="yes" required><span>Allow managed updates and backups<small>Belmelo can back up and update Home Assistant and its connector. System updates still need your chosen times and approval of a release in Belmelo.</small></span></label><button type="submit">Connect to Belmelo</button></form>'
    backup = recovery_page(companion.directory, selected)
    backup = backup[backup.index('<h1>'):].removesuffix('</html>').replace('<h1>Belmelo backup recovery</h1>', '<h2>Backup recovery</h2>', 1)
    return '''<!doctype html><html lang="en-AU"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Belmelo maintenance</title><style>
body{box-sizing:border-box;font:16px/1.55 system-ui,sans-serif;background:#fffcf6;color:#293747;max-width:42rem;margin:0 auto;padding:24px 18px}*{box-sizing:border-box}h1,h2{color:#183b63;line-height:1.25}h1{font-size:26px;margin:10px 0 16px}h2{font-size:19px}p{margin:10px 0 18px}.brand{font-weight:700;color:#183b63}.card{background:white;border:1px solid #e2e7eb;border-radius:18px;padding:22px;margin:16px 0}label{display:block;font-weight:600;margin:14px 0 8px}textarea,input:not([type=checkbox]){display:block;width:100%;min-width:0;border:1px solid #b7c6d2;border-radius:10px;padding:12px;font:16px/1.5 ui-monospace,monospace;color:#183b63}textarea{resize:vertical}.choice{display:flex;align-items:flex-start;gap:11px;font-size:15px}.choice input{width:21px;height:21px;margin:3px 0;flex-shrink:0}.choice small{display:block;font-size:13px;font-weight:400;color:#5c6b7a;margin-top:4px}button{width:100%;min-height:48px;margin-top:18px;border:0;border-radius:10px;padding:12px 16px;background:#183b63;color:white;font:600 16px system-ui;cursor:pointer}.state{display:inline-block;font-size:13px;color:#28634e;background:#eaf4ee;border-radius:20px;padding:5px 10px}.hint{font-size:13px;color:#5c6b7a}.error{padding:12px;border-radius:10px;background:#fff3e3;color:#795018}details{border-top:1px solid #e2e7eb;margin-top:20px;padding-top:12px}summary{min-height:44px;cursor:pointer;color:#183b63;font-weight:600}article{border-top:1px solid #e2e7eb;padding:12px 0}a{color:#183b63}:focus-visible{outline:3px solid #ec6b50;outline-offset:3px}@media(max-width:360px){body{padding:16px 12px}.card{padding:17px}}
</style></head><body><div class="brand">Belmelo · Maintenance Companion 0.2.1</div>''' + ('<p class="error" role="alert">' + escape(error) + '</p>' if error else '') + ('<section class="card"><span class="state">Connected to Belmelo</span><h1>You’re connected</h1><p>Return to Belmelo → Home → Devices → Device health → <b>Update times &amp; status</b> to choose your update window and approve a release.</p><p class="hint">System updates follow the window and release you approve in Belmelo.</p></section>' if connected else '<section class="card"><h1>' + ('Check your Belmelo connection' if paired else 'Connect to Belmelo') + '</h1><p>' + ('A connection has been saved. Belmelo has not confirmed a recent check yet. Use a fresh code below to reconnect.' if paired else 'Copy a maintenance code in Belmelo, paste it below, then connect. You do not need the Configuration tab.') + '</p>' + form + '</section>') + ('<details><summary>Reconnect maintenance</summary>' + form + '</details>' if connected else '') + '<details' + (' open' if selected else '') + '><summary>Backup recovery</summary>' + backup + '</details></body></html>'


class RecoveryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory, ingress_ip='172.30.32.2', companion=None):
        self.directory, self.ingress_ip = directory, ingress_ip
        self.companion, self.csrf = companion, secrets.token_urlsafe(32)
        self.setup_lock = threading.Lock()
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
            body, code = (maintenance_page(self.server.companion, self.server.csrf, selected) if self.server.companion else recovery_page(self.server.directory, selected)).encode(), 200
        self.reply(code, body)

    def do_POST(self):
        from .maintenance import MaintenanceError
        if self.client_address[0] != self.server.ingress_ip or urlsplit(self.path).path != '/pair' or self.server.companion is None:
            self.reply(403, b'Open this page inside Home Assistant.')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2048 or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type', '').split(';')[0] != 'application/x-www-form-urlencoded':
                raise ValueError()
            fields = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True, strict_parsing=True, max_num_fields=3)
            if set(fields) != {'csrf', 'pairing_code', 'allow_maintenance'} or any(len(v) != 1 for v in fields.values()) or not hmac.compare_digest(fields['csrf'][0], self.server.csrf):
                raise ValueError()
        except (ValueError, UnicodeError, TypeError):
            self.reply(403, b'Reopen the maintenance page and try again.')
            return
        if not self.server.setup_lock.acquire(blocking=False):
            self.reply(409, b'A connection is already being checked. Wait a moment.')
            return
        try:
            if not hmac.compare_digest(fields['csrf'][0], self.server.csrf):
                self.reply(403, b'Reopen the maintenance page and try again.')
                return
            self.server.companion.connect_setup(fields['pairing_code'][0], fields['allow_maintenance'][0] == 'yes')
            self.server.csrf = secrets.token_urlsafe(32)
        except MaintenanceError as error:
            self.reply(400, maintenance_page(self.server.companion, self.server.csrf, error=str(error)).encode())
            return
        except Exception:
            self.reply(400, maintenance_page(self.server.companion, self.server.csrf, error='The connection could not be confirmed. Create a fresh code in Belmelo and try again.').encode())
            return
        finally:
            self.server.setup_lock.release()
        self.reply(303, b'', './')

    def reply(self, code, body, location=None):
        self.send_response(code)
        if location:
            self.send_header('Location', location)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)


def start_recovery_ui(companion):
    server = RecoveryServer(('0.0.0.0', 8101), companion.directory, companion=companion)
    threading.Thread(target=server.serve_forever, name='homekind-local-recovery', daemon=True).start()
    return server
