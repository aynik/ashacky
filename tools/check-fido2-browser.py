#!/usr/bin/env python3
"""Attended localhost WebAuthn fixture. Requires python-fido2; never installed.

Only explicit browser button presses request authentication. The optional state
file contains one public credential for persistence checks, never a private key.
"""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from fido2.server import Fido2Server
from fido2.utils import websafe_decode, websafe_encode
from fido2.webauthn import AttestedCredentialData

PAGE = b'''<!doctype html><meta charset="utf-8"><title>Ashacky passkey test</title>
<style>body{font:18px system-ui;max-width:740px;margin:60px auto;padding:20px}button{font:inherit;margin:8px;padding:12px}pre{white-space:pre-wrap}</style>
<h1>Ashacky passkey test</h1><p>This localhost fixture checks a disposable passkey. The Mac handles Touch ID and its account-password fallback.</p>
<button onclick="run('register')">Create test passkey</button>
<button onclick="run('authenticate')">Sign in</button>
<button onclick="run('discover')">Discoverable sign-in</button>
<button onclick="controller?.abort()">Cancel request</button>
<pre id="status" role="status">Ready. Use Create first, then Sign in. Choose the security-key option if the browser asks.</pre>
<script>
let controller;
const status=document.getElementById('status');
const decode=s=>Uint8Array.from(atob(s.replaceAll('-','+').replaceAll('_','/')),c=>c.charCodeAt(0));
const encode=b=>b===null?null:btoa(String.fromCharCode(...new Uint8Array(b))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
async function post(path,value={}) { const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)}); const v=await r.json(); if(!r.ok)throw Error(v.error);return v; }
async function run(kind) {
 if(controller){status.textContent='Finish or cancel the pending request first.';return;}
 controller=new AbortController();
 try {
  status.textContent='Waiting for the browser and native Mac prompt...';
  const options=await post('/'+kind+'/begin'); const key=options.publicKey;
  key.challenge=decode(key.challenge); if(key.user)key.user.id=decode(key.user.id);
  for(const name of ['excludeCredentials','allowCredentials']) for(const c of key[name]||[])c.id=decode(c.id);
  const c=await navigator.credentials[kind==='register'?'create':'get']({publicKey:key,signal:controller.signal});
  const response={clientDataJSON:encode(c.response.clientDataJSON)};
  for(const name of ['attestationObject','authenticatorData','signature','userHandle'])if(name in c.response)response[name]=encode(c.response[name]);
  const value={id:c.id,rawId:encode(c.rawId),type:c.type,response,clientExtensionResults:c.getClientExtensionResults()};
  const result=await post('/'+kind+'/finish',value);status.textContent=result.result;
 } catch(e) {status.textContent=e.name+': '+e.message;} finally {controller=null;}
}
</script>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True, help='Private local file for this test public credential')
    args = parser.parse_args()
    os.umask(0o077)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    assert not args.state.is_symlink()
    credentials = [AttestedCredentialData(websafe_decode(args.state.read_text().strip()))] if args.state.exists() else []
    pending = {}
    server = None
    origin = ''

    def phase(message):
        print(datetime.now(timezone.utc).isoformat(timespec='seconds'), message, flush=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not log credentials, client data or public-key identifiers.

        def send(self, code, value, kind='application/json'):
            data = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            if self.path != '/' or self.headers.get('Host') != origin.removeprefix('http://'):
                self.send(404, {}); return
            self.send(200, PAGE, 'text/html; charset=utf-8')
            phase('Test page delivered; no authentication requested')

        def do_POST(self):
            try:
                if self.headers.get('Origin') != origin or self.headers.get('Host') != origin.removeprefix('http://'):
                    raise ValueError('Origin mismatch')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Invalid request length')
                value = json.loads(self.rfile.read(length))
                kind, action = self.path.strip('/').split('/')
                if kind not in ('register', 'authenticate', 'discover') or action not in ('begin', 'finish'):
                    raise ValueError('Unknown test operation')
                if action == 'begin':
                    phase(kind + ' begin')
                    pending.clear()
                    if kind == 'register':
                        options, state = server.register_begin(
                            dict(id=b'ashacky-local-test', name='Ashacky test', displayName='Local test account'),
                            resident_key_requirement='required', user_verification='required',
                            authenticator_attachment='cross-platform')
                    else:
                        if not credentials:
                            raise ValueError('Create a test passkey first')
                        options, state = server.authenticate_begin(credentials if kind == 'authenticate' else None,
                                                                  user_verification='required')
                    pending[kind] = state
                    self.send(200, dict(options)); return
                state = pending.pop(kind)
                if kind == 'register':
                    result = server.register_complete(state, value)
                    credentials[:] = [result.credential_data]
                    temporary = args.state.with_suffix('.new')
                    with temporary.open('x') as stream:
                        stream.write(websafe_encode(bytes(result.credential_data)) + '\n')
                    temporary.replace(args.state)
                    text = 'Registration verified with user presence and verification. Saved only the public credential locally.'
                else:
                    server.authenticate_complete(state, credentials, value)
                    text = 'Sign-in verified: fresh challenge, localhost RP/origin, signature, user presence and verification.'
                phase(text); self.send(200, dict(result=text))
            except Exception as error:
                # Exception type only; data/IDs remain out of the diagnostic log.
                phase('Test request rejected: ' + type(error).__name__)
                self.send(400, dict(error=type(error).__name__ + ': ' + str(error)))

    http = HTTPServer(('127.0.0.1', 0), Handler)
    origin = 'http://localhost:' + str(http.server_port)
    server = Fido2Server(dict(id='localhost', name='Ashacky local test'), verify_origin=lambda value: value == origin)
    print(origin + '/', flush=True)
    print('Waiting for attended browser input; Ctrl+C stops this fixture.', flush=True)
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        http.server_close()


if __name__ == '__main__':
    main()
