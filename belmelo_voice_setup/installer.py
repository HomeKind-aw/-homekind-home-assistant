"""One-time ingress installer for our integration and optional pilot models."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import hmac
import hashlib
import html
import json
import secrets
import shutil
import time
from urllib.parse import parse_qs

NONCE = secrets.token_urlsafe(32)
MODEL_SOURCE = Path('/opt/belmelo/wake_words')
MODEL_IDS = {'belmelo_hey_belle_pilot_v1':'Hey Belle', 'belmelo_okay_belle_pilot_v1':'Okay Belle'}


def install_models(source=MODEL_SOURCE, config=Path('/homeassistant')):
    """Add only the fixed, hash-verified package; never activate a microphone."""
    if source.is_symlink() or not source.is_dir() or any(p.is_symlink() for p in source.rglob('*')):
        raise ValueError('The packaged wake words need a local check.')
    release = json.loads((source/'release.json').read_text())
    if release.get('status') != 'experimental' or release.get('physical_acceptance') is not False:
        raise ValueError('The pilot status could not be verified.')
    models = release.get('models', [])
    ids={m.get('id') for m in models}
    if not 1<=len(models)<=len(MODEL_IDS) or len(ids)!=len(models) or not ids.issubset(MODEL_IDS):
        raise ValueError('The pilot catalogue could not be verified.')
    payload = {}
    for entry in models:
        word_id = entry['id']
        expected = {word_id+'.json', word_id+'.tflite'}
        if set(entry.get('files', {})) != expected:
            raise ValueError('The pilot files could not be verified.')
        for name, digest in entry['files'].items():
            content = (source/name).read_bytes()
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), digest):
                raise ValueError('A pilot file failed its integrity check.')
            payload[name] = content
        manifest = json.loads(payload[word_id+'.json'])
        if manifest.get('type') != 'micro' or manifest.get('version') != 2 or manifest.get('wake_word') != MODEL_IDS[word_id] or manifest.get('model') != word_id+'.tflite':
            raise ValueError('The wake-word manifest could not be verified.')
        model = payload[word_id+'.tflite']
        if not 100 < len(model) < 262144 or model[4:8] != b'TFL3':
            raise ValueError('The wake-word model could not be verified.')
    target = config/'custom_wake_words'
    if config.is_symlink() or target.is_symlink():
        raise ValueError('The custom wake-word folder needs a local check.')
    # Validate every destination before creating or replacing anything. A local
    # custom model with our ID is preserved, never silently overwritten.
    for name, content in payload.items():
        existing = target/name
        if existing.is_symlink() or existing.exists() and (not existing.is_file() or existing.read_bytes() != content):
            raise ValueError('An existing wake word with this ID has been preserved.')
    target.mkdir(exist_ok=True)
    staged = target/('.belmelo_setup_'+secrets.token_hex(8))
    created = []
    staged.mkdir()
    try:
        for name, content in payload.items():
            if (target/name).exists():
                continue
            (staged/name).write_bytes(content)
        for path in list(staged.iterdir()):
            destination = target/path.name
            # The destination was absent at preflight; do not overwrite a
            # concurrent local write between validation and publication.
            destination.hardlink_to(path)
            created.append(destination)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staged)
    return len(created)

def install(source=Path('/opt/belmelo/custom_components/belmelo_voice'), config=Path('/homeassistant'), data=Path('/data'), *, auto_wake=False):
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
        if auto_wake or (target / 'prepare_hey_belle').is_file():
            (staged / 'prepare_hey_belle').touch()
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

def prepare(*, pilot=False, source=Path('/opt/belmelo/custom_components/belmelo_voice'),
            models=MODEL_SOURCE, config=Path('/homeassistant'), data=Path('/data')):
    """One preparation action and one restart, with explicit optional pilot choice."""
    target = config/'custom_wake_words'
    before = {p.name for p in target.iterdir()} if target.is_dir() else set()
    created = []
    try:
        if pilot:
            install_models(models, config)
            created = [p for p in target.iterdir() if p.name not in before
                and p.name in {word+suffix for word in MODEL_IDS for suffix in ('.json', '.tflite')}]
        install(source, config, data, auto_wake=pilot)
    except Exception:
        # Roll back only exact package files newly added by this action.
        for path in created:
            packaged = models/path.name
            if not path.is_symlink() and path.is_file() and path.read_bytes() == packaged.read_bytes():
                path.unlink()
        raise

class Handler(BaseHTTPRequestHandler):
    def page(self, message='', status=200):
        body = ('<!doctype html><html lang="en-AU"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Belmelo Voice setup</title><style>body{font:18px system-ui;line-height:1.55;background:#faf8f4;color:#333;margin:0;padding:24px}main{max-width:620px;margin:auto}button{padding:14px 22px;border:0;border-radius:16px;background:#665180;color:white;font:inherit}li{margin:12px 0}.status{padding:16px;background:#ece7f2;border-radius:16px}</style><main>'
            '<h1>Prepare Belle</h1><p>Set up Hey Belle for your room microphones.</p>'
            + ('<p class="status" role="status">' + html.escape(message) + '</p>' if message else '')
            + '<form method="post"><input type="hidden" name="nonce" value="' + NONCE + '"><input type="hidden" name="operation" value="prepare">'
            + ('<input type="hidden" name="pilot" value="yes">' if (MODEL_SOURCE/'release.json').is_file() else '')
            + '<button>Prepare Belle</button></form>'
            '<ol><li>Restart Home Assistant once from Settings → System after preparation.</li><li><a href="https://app.belmelo.com/household?room-microphones=1" target="_blank" rel="noopener">Return to room microphones in Belmelo</a>. Your saved connection picks up from there.</li></ol>'
            '<details><summary>What happens next?</summary><p>After the restart, compatible microphones connected to Belle will select Hey Belle automatically for their first setup. Pairing and sharing choices stay in place. You can change the phrase later in Belmelo.</p></details>'
            + '</main></html>').encode()
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
            operation=form.get('operation', ['integration'])[0]
            if operation=='prepare':
                if form.get('pilot', [''])[0] not in ('', 'yes'):
                    self.page('Choose whether to include the optional pilot.', 400)
                    return
                prepare(pilot=form.get('pilot', [''])[0]=='yes')
                self.page('Belle is prepared. Restart Home Assistant once, then return to Belmelo. Your saved microphone pairing is kept.')
            elif operation=='models':
                install_models()
                self.page('Pilot wake words added. Restart Home Assistant once, then choose a phrase and test it in Belmelo. Existing microphone pairing is kept.')
            elif operation=='integration':
                install()
                self.page('Integration installed. Restart Home Assistant once. Existing pairing is kept; new microphones need a pairing code.')
            else:
                self.page('Open setup again to continue.', 400)
        except Exception:
            self.page('Installation needs a local check. The existing Home Assistant configuration has been preserved.', 500)

    def log_message(self, format, *args):
        # No form fields, pairing codes or configuration in logs.
        return

if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
