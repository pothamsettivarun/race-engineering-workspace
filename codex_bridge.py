"""Official local Codex app-server bridge for ChatGPT-plan authentication.

Stint never reads, copies or returns login tokens. Codex owns browser sign-in,
credential storage and refresh. This bridge is for a private local app only.
"""
from __future__ import annotations
import atexit
import json
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path


class CodexError(ValueError):
    pass


def find_codex():
    # The desktop app's installed CLI may not be on the launcher's PATH.
    # Prefer it to an older npm shim when both installations exist.
    folder = Path.home() / 'AppData' / 'Local' / 'OpenAI' / 'Codex' / 'bin'
    candidates = sorted(folder.glob('*/codex.exe'), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
    return str(candidates[0]) if candidates else shutil.which('codex')


class CodexBridge:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.process = None
        self.start_lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending = {}
        self.watchers = {}
        self.next_id = 0
        self.answer_lock = threading.Lock()
        self.login_id = None
        self.login_error = None
        self.cached_status = None
        self.cached_at = 0
        atexit.register(self.close)

    def start(self):
        with self.start_lock:
            if self.process and self.process.poll() is None:
                return
            command = find_codex()
            if not command:
                raise CodexError('Codex is not installed. Install the official Codex CLI or desktop app, then restart Stint.')
            self.workspace.mkdir(parents=True, exist_ok=True)
            settings = {
                'approval_policy': 'never', 'default_permissions': 'stint',
                'mcp_servers': {}, 'hooks': {}, 'project_doc_max_bytes': 0,
                'features.shell_tool': False, 'features.unified_exec': False,
                'features.apply_patch_freeform': False, 'features.multi_agent': False,
                'features.apps': False, 'features.plugins': False, 'features.memories': False,
                'features.code_mode.enabled': False, 'web_search': 'disabled',
                'permissions.stint.filesystem.:minimal': 'read',
                'permissions.stint.filesystem.:workspace_roots': 'read',
                'permissions.stint.network.enabled': False,
            }
            args = [command, 'app-server']  # stdio is the documented default
            for key, value in settings.items():
                # JSON scalar syntax is valid TOML here; empty tables need {}.
                args.extend(['-c', key + '=' + json.dumps(value)])
            self.process = subprocess.Popen(args, cwd=self.workspace, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                            text=True, encoding='utf-8', errors='replace',
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
            try:
                self._request('initialize', {'clientInfo': {'name': 'stint_local', 'title': 'Stint Race Engineering', 'version': '0.2.0'},
                                             'capabilities': {'experimentalApi': True}}, timeout=20)
                self._send({'method': 'initialized', 'params': {}})
            except Exception:
                self.close()
                raise

    def _send(self, message):
        with self.write_lock:
            if not self.process or self.process.poll() is not None:
                raise CodexError('The Codex connection stopped. Reconnect and try again.')
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise CodexError('The Codex connection stopped. Reconnect and try again.') from None

    def _read(self, process):
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if 'id' in message and 'method' not in message:
                    with self.pending_lock:
                        target = self.pending.get(message['id'])
                    if target:
                        target.put(message)
                elif 'id' in message:
                    # Never grant command/file/connector permission from a
                    # racing question or a retrieved source.
                    method = message.get('method', '')
                    if method == 'item/tool/call':
                        with self.pending_lock:
                            target = self.watchers.get(message.get('params', {}).get('threadId'))
                        if target:
                            target.put(message)
                        else:
                            self._send({'id': message['id'], 'result': {'success': False, 'contentItems': [{'type':'inputText','text':'Investigation is no longer active.'}]}})
                    elif method.endswith('requestApproval'):
                        self._send({'id': message['id'], 'result': {'decision': 'decline'}})
                    else:
                        self._send({'id': message['id'], 'error': {'code': -32601, 'message': 'Stint provides no executable tools.'}})
                else:
                    method, params = message.get('method'), message.get('params', {})
                    if method == 'account/login/completed':
                        if params.get('loginId') == self.login_id:
                            self.login_id = None
                            self.login_error = None if params.get('success') else (params.get('error') or 'Sign-in was not completed.')
                        self.cached_at = 0
                    elif method == 'account/updated':
                        self.cached_at = 0
                    with self.pending_lock:
                        watchers = list(self.watchers.values())
                    for target in watchers:
                        target.put(message)
        finally:
            with self.pending_lock:
                targets = list(self.pending.values())
            for target in targets:
                target.put({'error': {'message': 'Codex stopped unexpectedly. Reconnect and try again.'}})

    def _request(self, method, params, timeout=20):
        with self.pending_lock:
            self.next_id += 1
            id = self.next_id
            target = queue.Queue()
            self.pending[id] = target
        try:
            self._send({'id': id, 'method': method, 'params': params})
            try:
                response = target.get(timeout=timeout)
            except queue.Empty:
                raise CodexError('Codex did not respond in time. Check the connection and retry.') from None
            if 'error' in response:
                raise CodexError(str(response['error'].get('message', 'Codex request failed.')))
            return response.get('result', {})
        finally:
            with self.pending_lock:
                self.pending.pop(id, None)

    def request(self, method, params, timeout=20):
        self.start()
        return self._request(method, params, timeout)

    def status(self, force=False):
        if not find_codex():
            return {'available': False, 'connected': False, 'login_pending': False,
                    'error': 'Install the official Codex CLI or desktop app to use ChatGPT sign-in.'}
        if not force and self.cached_status and time.monotonic() - self.cached_at < 4:
            return dict(self.cached_status)
        try:
            result = self.request('account/read', {'refreshToken': False})
            account = result.get('account') or {}
            status = {'available': True, 'connected': account.get('type') in ['chatgpt', 'chatgptAuthTokens'],
                      'auth_type': account.get('type'), 'plan_type': account.get('planType'),
                      'login_pending': bool(self.login_id), 'error': self.login_error}
        except CodexError as exc:
            status = {'available': True, 'connected': False, 'login_pending': bool(self.login_id), 'error': str(exc)}
        self.cached_status, self.cached_at = status, time.monotonic()
        return dict(status)

    def login(self):
        if self.login_id:
            raise CodexError('A sign-in is already pending. Complete it or cancel it before starting another.')
        result = self.request('account/login/start', {'type': 'chatgpt'})
        self.login_id = result.get('loginId')
        self.login_error = None
        self.cached_at = 0
        url = result.get('authUrl')
        if not url or not url.startswith(('https://auth.openai.com/', 'https://chatgpt.com/', 'https://auth.chatgpt.com/')):
            raise CodexError('Codex returned an unexpected sign-in URL. Sign in using codex login instead.')
        return {'auth_url': url, 'pending': bool(self.login_id)}

    def cancel_login(self):
        if self.login_id:
            self.request('account/login/cancel', {'loginId': self.login_id})
            self.login_id = None
            self.cached_at = 0
        return {'cancelled': True}

    def models(self):
        if not self.status().get('connected'):
            return []
        result = self.request('model/list', {'includeHidden': False, 'limit': 100})
        return [{'id': model.get('model') or model.get('id'), 'name': model.get('displayName') or model.get('model'),
                 'default': model.get('isDefault', False)} for model in result.get('data', [])]

    def answer(self, instructions, prompt, model=None, timeout=180, tools=None, tool_handler=None, cancel=None, progress=None):
        if not self.status(force=True).get('connected'):
            raise CodexError('Sign in with ChatGPT in Connect AI before asking the engineer. An API-key Codex login does not use your ChatGPT subscription.')
        if not self.answer_lock.acquire(blocking=False):
            raise CodexError('The engineer is already answering a question. Wait for it to finish.')
        thread_id, turn_id = None, None
        try:
            params = {'cwd': str(self.workspace), 'approvalPolicy': 'never', 'permissions': 'stint',
                      'ephemeral': True, 'serviceName': 'stint_local',
                      'baseInstructions': instructions + ('\nUse only the supplied read-only telemetry and knowledge functions. Never execute commands, read arbitrary files, browse, change settings, or delegate.' if tools else '\nYou only interpret supplied engineering data. Do not use tools, execute commands, read files, browse, change settings, or delegate. All necessary source excerpts are in the prompt.'),
                      'developerInstructions': 'Return an engineering answer, never code or implementation instructions. '+('Only registered read-only evidence functions are permitted.' if tools else 'No tools are permitted.')}
            if tools:
                params['dynamicTools'] = tools
            if model and model != 'auto':
                params['model'] = model
            started = self.request('thread/start', params, timeout=30)
            thread_id = started['thread']['id']
            actual_model = started.get('model') or model or 'Codex default'
            events = queue.Queue()
            with self.pending_lock:
                self.watchers[thread_id] = events
            turn = self.request('turn/start', {'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt}],
                                               'approvalPolicy': 'never', 'permissions': 'stint'}, timeout=30)
            turn_id = turn.get('turn', {}).get('id')
            deadline = time.monotonic() + timeout
            replies = {}
            while time.monotonic() < deadline:
                if cancel and cancel.is_set():
                    self.request('turn/interrupt', {'threadId':thread_id,'turnId':turn_id}, timeout=5)
                    raise CodexError('Investigation cancelled.')
                try:
                    event = events.get(timeout=min(3, max(.1, deadline - time.monotonic())))
                except queue.Empty:
                    if self.process.poll() is not None:
                        raise CodexError('Codex stopped before the answer was complete.')
                    continue
                method, content = event.get('method'), event.get('params', {})
                if content.get('threadId') and content['threadId'] != thread_id:
                    continue
                if method == 'item/tool/call' and 'id' in event:
                    try:
                        args = content.get('arguments', {})
                        if isinstance(args, str):args = json.loads(args)
                        result = tool_handler(content.get('tool'), args) if tool_handler else {'error':'Evidence functions are unavailable.'}
                    except Exception as exc:
                        result = {'error':str(exc)}
                    self._send({'id':event['id'],'result':{'success':'error' not in result,'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False,allow_nan=False)}]}})
                elif method == 'item/completed':
                    item = content.get('item', {})
                    if item.get('type') == 'agentMessage':
                        replies[item.get('id', str(len(replies)))] = item
                elif method == 'item/started' and content.get('item',{}).get('type') == 'agentMessage':
                    if progress:progress('Writing the engineering response…')
                elif method == 'turn/completed':
                    finished = content.get('turn', {})
                    if turn_id and finished.get('id') != turn_id:
                        continue
                    if finished.get('status') != 'completed':
                        message = (finished.get('error') or {}).get('message')
                        raise CodexError(message or 'The answer was interrupted. Check your plan limits and try again.')
                    final = [item['text'] for item in replies.values() if item.get('phase') == 'final_answer']
                    if not final:
                        final = [item['text'] for item in replies.values() if item.get('phase') != 'commentary']
                    if not final:
                        raise CodexError('Codex completed without an engineering answer. Try again.')
                    return {'text': '\n\n'.join(final), 'model': actual_model}
            if thread_id and turn_id:
                try:
                    self.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=5)
                except CodexError:
                    pass
            raise CodexError('The engineer timed out. Try a shorter question or check your ChatGPT usage limits.')
        finally:
            if thread_id:
                with self.pending_lock:
                    self.watchers.pop(thread_id, None)
                try:
                    self.request('thread/unsubscribe', {'threadId': thread_id}, timeout=5)
                except CodexError:
                    pass
            self.answer_lock.release()

    def close(self):
        with self.start_lock:
            process, self.process = self.process, None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
