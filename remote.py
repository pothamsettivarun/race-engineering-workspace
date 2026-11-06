"""Private, allowlisted pitwall gateway. Workspace administration stays local."""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import threading
import time
from email.header import decode_header, make_header
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from live import write_json
from pit_control import PitError

DEFAULTS = {'enabled': False, 'origin': '', 'users': []}


def validate_settings(value):
    if not isinstance(value, dict): raise PitError('Remote settings must be an object.', 400)
    enabled, origin, users = value.get('enabled', False), value.get('origin', ''), value.get('users', [])
    if type(enabled) is not bool or not isinstance(origin, str) or not isinstance(users, list) or len(users) > 32:
        raise PitError('Choose valid remote settings and up to 32 allowed users.', 400)
    origin = origin.strip().rstrip('/')
    if origin:
        try:
            p = urlparse(origin)
            if (p.scheme != 'https' or not p.hostname or not re.fullmatch(r'[a-z0-9.-]+\.ts\.net', p.hostname) or
                p.username or p.password or p.path or p.query or p.fragment or p.port not in (None, 443, 8443)):
                raise ValueError()
        except ValueError: raise PitError('Use the HTTPS Tailscale Serve address on port 8443 or 443.', 400)
        origin = f'https://{p.hostname}' + (f':{p.port}' if p.port and p.port != 443 else '')
    if enabled and not origin: raise PitError('Set the Tailscale HTTPS address before enabling remote access.', 400)
    normalized, seen = [], set()
    for u in users:
        if not isinstance(u, dict): raise PitError('Each allowed user needs a login and role.', 400)
        login, role, name = u.get('login', ''), u.get('role'), u.get('name', '')
        if not isinstance(login, str) or not isinstance(name, str): raise PitError('User logins and names must be text.', 400)
        login = login.strip().casefold()
        if not login or len(login) > 200 or any(ord(c) < 33 for c in login) or login in seen or role not in ('viewer', 'engineer'):
            raise PitError('Use unique Tailscale logins and viewer or engineer roles.', 400)
        if len(name) > 120 or any(ord(c) < 32 for c in name): raise PitError('Names must be under 120 characters.', 400)
        seen.add(login); normalized.append({'login': login, 'role': role, 'name': name.strip() or login})
    if sum(u['role'] == 'engineer' for u in normalized) > 1:
        raise PitError('Designate only one engineer; everyone else is a viewer.', 400)
    return {'enabled': enabled, 'origin': origin, 'users': normalized}


def render_page(root, filename):
    page = (root/'web'/filename).read_text(encoding='utf-8')
    if '{{LIVE_PANEL}}' in page:
        page = page.replace('{{LIVE_PANEL}}', (root/'web'/'live-panel.html').read_text(encoding='utf-8'))
    if '{{LAP_COMPARISON_PANEL}}' in page:
        page = page.replace('{{LAP_COMPARISON_PANEL}}', (root/'web'/'lap-comparison.html').read_text(encoding='utf-8'))
    return page.encode('utf-8')


class RemoteGateway:
    def __init__(self, data, root, live, *, port=8766):
        self.data, self.root, self.live, self.port = Path(data), Path(root), live, port
        self.settings = copy.deepcopy(DEFAULTS)
        self.settings_error = ''
        self.http = None
        self.thread = None
        self.streams = {}
        self.clients = {}
        self.transport_cache = None
        self.transport_at = -100
        self.transport_lock = threading.Lock()
        self.configure_lock = threading.Lock()
        try:
            path = self.data/'remote-settings.json'
            if path.exists(): self.settings = validate_settings(json.loads(path.read_text(encoding='utf-8')))
        except (ValueError, OSError):
            self.settings_error = 'Saved remote settings were invalid. Remote access is disabled.'

    def start(self):
        if not self.settings['enabled'] or self.http: return
        http = ThreadingHTTPServer(('127.0.0.1', self.port), RemoteHandler)
        http.daemon_threads = True
        http.gateway = self
        self.http = http
        self.thread = threading.Thread(target=http.serve_forever, name='stint-remote', daemon=True)
        self.thread.start()

    def configure(self, value):
        settings = validate_settings(value)
        with self.configure_lock:
            return self._configure(settings)

    def _configure(self, settings):
        with self.live.lock:
            old = self.settings
            previous_listener = self.http
            self.settings = settings
            try:
                if settings['enabled']: self.start()
                write_json(self.data/'remote-settings.json', settings)
            except OSError:
                self.settings = old
                if previous_listener is None and self.http is not None: self.stop_listener()
                raise PitError('Remote listener could not start or settings could not be saved. Check port 8766 and folder access.', 400)
            self.settings_error = ''
            old_engineer = next((u['login'] for u in old['users'] if u['role'] == 'engineer'), None)
            if not settings['enabled'] or self.engineer() != old_engineer or settings['origin'] != old['origin']:
                self.live.pit.reset('Remote access or the designated engineer changed.')
                self.live._checkpoint()
            elif self.live.pit.enabled:
                self.live.pit.engineer_name = self.engineer_name()
        if not settings['enabled']: self.stop_listener()
        return self.status()

    def engineer(self):
        return next((u['login'] for u in self.settings['users'] if u['role'] == 'engineer'), None)

    def engineer_name(self):
        return next((u['name'] for u in self.settings['users'] if u['role'] == 'engineer'), None)

    def member(self, login):
        with self.live.lock:
            if not self.settings['enabled']: return None
            return next((copy.deepcopy(u) for u in self.settings['users'] if u['login'] == login), None)

    def status(self):
        with self.live.lock:
            now = time.monotonic()
            return {**copy.deepcopy(self.settings), 'engineer': self.engineer(), 'engineer_name': self.engineer_name(), 'listener_port': self.port,
                    'listener_running': self.http is not None, 'error': self.settings_error,
                    'clients': [copy.deepcopy(c) for c in self.clients.values() if now-c['last_seen'] < 15],
                    'streams': len(self.streams), 'pit_control': self.live.pit.status()}

    def touch(self, member):
        with self.live.lock:
            # At most 32 allowlisted identities; no arbitrary client state.
            self.clients[member['login']] = {**member, 'last_seen': time.monotonic()}
            self.clients = {k: v for k, v in self.clients.items() if self.member(k)}

    def transport(self):
        """Inspect only; never change another app's Serve configuration."""
        with self.transport_lock:
            if time.monotonic()-self.transport_at < 10 and self.transport_cache is not None: return copy.deepcopy(self.transport_cache)
            executable = shutil.which('tailscale')
            if not executable:
                candidate = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Tailscale'/'tailscale.exe'
                if candidate.is_file(): executable = str(candidate)
            result = {'installed': bool(executable), 'signed_in': False, 'origin': '', 'message': 'Install Tailscale on the racing and engineer PCs.'}
            if executable:
                try:
                    options = {'capture_output': True, 'text': True, 'encoding': 'utf-8', 'timeout': 4}
                    if os.name == 'nt': options['creationflags'] = subprocess.CREATE_NO_WINDOW
                    process = subprocess.run([executable, 'status', '--json'], **options)
                    state = json.loads(process.stdout) if process.returncode == 0 else {}
                    result['signed_in'] = state.get('BackendState') == 'Running'
                    dns = (state.get('Self', {}).get('DNSName') or '').rstrip('.')
                    if result['signed_in'] and dns:
                        result['origin'] = f'https://{dns}:8443'
                        result['message'] = 'Signed in. Configure private Serve and share the racing PC with your engineer.'
                    else: result['message'] = 'Open Tailscale and sign in on this PC.'
                    process = subprocess.run([executable, 'serve', 'status', '--json'], **options)
                    serve = json.loads(process.stdout) if process.returncode == 0 else {}
                    target = 'http://127.0.0.1:'+str(self.port)
                    result['serve_configured'] = any(k.endswith(':8443') and v.get('Handlers', {}).get('/', {}).get('Proxy') == target
                                                    for k, v in (serve.get('Web') or {}).items())
                    result['public_funnel'] = any(k.endswith(':8443') and bool(v) for k, v in (serve.get('AllowFunnel') or {}).items())
                    if result['public_funnel']: result['message'] = 'Public Funnel is enabled on port 8443. Disable it before using private pitwall access.'
                    elif result['signed_in'] and result['serve_configured']: result['message'] = 'Private Serve is configured. Share the racing PC and enable remote access below.'
                    elif process.returncode != 0: result['message'] = 'Could not inspect Serve settings. Run the setup helper before enabling remote access.'
                except (ValueError, OSError, subprocess.TimeoutExpired): result['message'] = 'Tailscale status could not be read. Check that Tailscale is running.'
            self.transport_cache, self.transport_at = result, time.monotonic()
            return copy.deepcopy(result)

    def stop_listener(self):
        http, self.http = self.http, None
        if http:
            http.shutdown(); http.server_close()
            if self.thread: self.thread.join(timeout=2)
        self.thread = None

    def close(self):
        with self.live.lock:
            self.settings['enabled'] = False
            self.live.pit.reset('The remote gateway stopped.')
        self.stop_listener()


class RemoteHandler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # Identity headers and telemetry are never request logs.

    def reply(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), 'application/json; charset=utf-8', status)

    def send_bytes(self, content, mime, status=200):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers(); self.wfile.write(content)

    def authorize(self, write=False):
        g = self.server.gateway
        if not g.settings['enabled']: raise PitError('Remote pitwall access is disabled.', 403)
        if self.client_address[0] != '127.0.0.1': raise PitError('A private Tailscale Serve connection is required.', 403)
        host = urlparse(g.settings['origin']).netloc
        if self.headers.get('Host') not in {host, f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
            raise PitError('Unexpected pitwall host.', 403)
        origin = self.headers.get('Origin')
        if (write and origin != g.settings['origin']) or (origin and origin != g.settings['origin']):
            raise PitError('Use the configured HTTPS pitwall address.', 403)
        if write and self.headers.get_content_type() != 'application/json': raise PitError('Send JSON pitwall requests.', 400)
        if write and self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin'): raise PitError('Cross-site changes are not allowed.', 403)
        raw = self.headers.get('Tailscale-User-Login', '')
        try: login = str(make_header(decode_header(raw))).strip().casefold()
        except (ValueError, LookupError): login = ''
        member = g.member(login)
        if not member: raise PitError('Your Tailscale login has not been granted pitwall access.', 403 if login else 401)
        g.touch(member)
        return member

    def do_GET(self):
        try:
            member = self.authorize()
            g, url = self.server.gateway, urlparse(self.path)
            if url.path == '/api/remote/me':
                self.reply({**member, 'engineer': g.engineer(), 'engineer_name': g.engineer_name(), 'origin': g.settings['origin']})
            elif url.path == '/api/live/snapshot': self.reply(g.live.snapshot(include_trace=True))
            elif url.path == '/api/live/pit': self.reply(g.live.pit.status())
            elif url.path == '/api/live/events': self.events(member)
            elif url.path == '/api/live/history':
                q = parse_qs(url.query); before = q.get('before', [None])[0]
                if before is not None and (not before.isdigit() or len(before) > 12): raise PitError('Invalid history cursor.', 400)
                self.reply(g.live.history(q.get('id', [None])[0], q.get('kind', [None])[0], int(before) if before is not None else None))
            else:
                filename = {'/': 'remote.html', '/live-boot.js': 'live-boot.js', '/live.js': 'live.js',
                            '/live-controls.js': 'live-controls.js', '/style.css': 'style.css'}.get(url.path)
                if not filename: raise PitError('Not found.', 404)
                content = render_page(g.root, filename) if filename.endswith('.html') else (g.root/'web'/filename).read_bytes()
                mime = 'text/javascript; charset=utf-8' if filename.endswith('.js') else 'text/css; charset=utf-8' if filename.endswith('.css') else 'text/html; charset=utf-8'
                self.send_bytes(content, mime)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        except PitError as exc: self.reply({'error': str(exc)}, exc.status)
        except (ValueError, OSError): self.reply({'error': 'Pitwall data could not be read.'}, 400)

    def events(self, member):
        g = self.server.gateway
        token = object()
        with g.live.lock:
            if len(g.streams) >= 8: raise PitError('All eight remote streams are in use. Close an unused pitwall tab.', 429)
            g.streams[token] = member['login']
            origin = g.settings['origin']
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers(); self.connection.settimeout(2)
            while not g.live.stopped.is_set():
                current = g.member(member['login'])
                if not current or current['role'] != member['role'] or g.settings['origin'] != origin: break
                g.touch(current)
                snap = g.live.snapshot()
                content = json.dumps(snap, ensure_ascii=False, allow_nan=False)
                self.wfile.write(f'event: snapshot\ndata: {content}\n\n'.encode()); self.wfile.flush()
                g.live.stopped.wait(.1)
        except (OSError, TimeoutError): pass
        finally:
            with g.live.lock: g.streams.pop(token, None)
            self.close_connection = True

    def do_POST(self):
        try:
            member = self.authorize(write=True)
            g, path = self.server.gateway, urlparse(self.path).path
            if path not in ('/api/live/plan', '/api/live/pit'): raise PitError('Not found.', 404)
            if member['role'] != 'engineer': raise PitError('Only the designated engineer can make changes.', 403)
            try: length = int(self.headers.get('Content-Length', '0'))
            except ValueError: raise PitError('Invalid request length.', 400)
            if not 0 < length <= 100_000: raise PitError('Request size is invalid.', 400)
            self.connection.settimeout(3)
            raw = self.rfile.read(length)
            if len(raw) != length: raise PitError('Request was interrupted.', 400)
            body = json.loads(raw)
            if not isinstance(body, dict): raise PitError('Send an object.', 400)
            with g.live.lock:
                # Recheck after reading: a local revoke must take effect immediately.
                current = self.authorize(write=True)
                if current['role'] != 'engineer': raise PitError('Engineer access was revoked.', 403)
                if path == '/api/live/pit': result = g.live.pit.submit(body, current['login'], actor_name=current['name'])
                else:
                    if type(body.get('revision')) is not int: raise PitError('The current plan revision is required.', 400)
                    result = g.live.update_plan(body.get('plan'), body.get('session_id'), body['revision'], member['login'])
            self.reply(result)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError): pass
        except PitError as exc: self.reply({'error': str(exc)}, exc.status)
        except (ValueError, OSError): self.reply({'error': 'Invalid or interrupted pitwall request.'}, 400)
