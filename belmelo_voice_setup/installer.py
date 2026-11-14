"""One-time ingress installer. Writes only our integration; never restarts Core."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import hmac
import html
import json
import secrets
import shutil
import time
from urllib.parse import parse_qs

NONCE = secrets.token_urlsafe(32)

def install(source=Path('/opt/belmelo/custom_components/belmelo_voice'), config=Path('/homeassistant'), data=Path('/data')):
    if source.is_symlink() or not source.is_dir():
        raise ValueError('The packaged integration is unavailable.')
    files = list(source.rglob('*'))
    if any(p.is_symlink() for p in files):
        raise ValueError('The integration package must contain regular files.')
    if json.loads((source / 'manifest.json').read_text()).get('domain') != 'belmelo_voice':
        raise ValueError('The packaged integration could not be verified.')
    parent = config / 'custom_components'
    if config.is_symlink() or parent.is_symlink():
        raise ValueError('The Home Assistant configuration folder needs a local check.')
    parent.mkdir(exist_ok=True)
    target = parent / 'belmelo_voice'
    if target.is_symlink() or target.exists() and any(p.is_symlink() for p in target.rglob('*')):
        raise ValueError('The existing integration folder needs a local check.')
    if target.exists():
        try:
            if json.loads((target / 'manifest.json').read_text()).get('domain') != 'belmelo_voice':
                raise ValueError('This folder belongs to a different integration.')
        except (OSError, json.JSONDecodeError) as err:
            raise ValueError('The existing integration could not be verified.') from err
    staged = parent / ('belmelo_voice_setup_' + secrets.token_hex(8))
    shutil.copytree(source, staged)
    try:
        if target.exists():
            data.mkdir(exist_ok=True)
            shutil.copytree(target, data / ('belmelo_voice_backup_' + str(time.time_ns())))
            old = parent / ('belmelo_voice_previous_' + secrets.token_hex(8))
            target.rename(old)
            try:
                staged.rename(target)
            except OSError:
                old.rename(target)
                raise
            shutil.rmtree(old)
        else:
            staged.rename(target)
    finally:
        if staged.exists():
            shutil.rmtree(staged)

class Handler(BaseHTTPRequestHandler):
    def page(self, message='', status=200):
        body = ('<!doctype html><html lang="en-AU"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Belmelo Voice setup</title><style>body{font:18px system-ui;line-height:1.55;background:#faf8f4;color:#333;margin:0;padding:24px}main{max-width:620px;margin:auto}button{padding:14px 22px;border:0;border-radius:16px;background:#665180;color:white;font:inherit}li{margin:12px 0}.status{padding:16px;background:#ece7f2;border-radius:16px}</style><main>'
            '<h1>Connect your microphone to Belle</h1><p>This installs the Belmelo Voice integration. Your existing microphone firmware and home connection stay in place.</p>'
            + ('<p class="status" role="status">' + html.escape(message) + '</p>' if message else '')
            + '<form method="post"><input type="hidden" name="nonce" value="' + NONCE + '"><button>Install integration</button></form>'
            '<ol><li>After installation, restart Home Assistant once from Settings → System.</li><li>Open Belmelo → Ask Belle → More options → Room microphones. Choose the room and features, then create a pairing code.</li><li>In Home Assistant, add the Belmelo Voice integration under Devices &amp; services. Paste the code and select the matching ESPHome microphone.</li><li>Check that the microphone uses its dedicated Belle assistant. Try a light command and check both the lights and reply.</li></ol>'
            '<p>You can remove this setup app after pairing. Pause room microphone access at any time in Belmelo.</p></main></html>').encode()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.page()

    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 512:
                self.page('Open setup again to continue.', 400)
                return
            form = parse_qs(self.rfile.read(size).decode())
            if not hmac.compare_digest(form.get('nonce', [''])[0], NONCE):
                self.page('Open setup again to continue.', 403)
                return
            install()
            self.page('Integration installed. Restart Home Assistant once, then add Belmelo Voice using your pairing code.')
        except Exception:
            self.page('Installation needs a local check. The existing Home Assistant configuration has been preserved.', 500)

    def log_message(self, format, *args):
        # No form fields, pairing codes or configuration in logs.
        return

if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
