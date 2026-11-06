"""HTTPS browser acceptance with a fake Tailscale identity proxy and fake SDK."""
import datetime
import http.client
import json
import ssl
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from live_fuel import totals_from
from test_remote import FakeSDK, ENGINEER, ORIGIN


class BrowserSDK(FakeSDK):
    def __init__(self):
        super().__init__()
        self.pressures={c:159 for c in self.pressures}
        self.fitted_pressures=dict(self.pressures)

    def frame(self,t):
        frame=super().frame(t)
        frame['values'].update({c.upper()+'coldPressure':p for c,p in self.fitted_pressures.items()})
        return frame


class IdentityProxy(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):self.forward()
    def do_POST(self):self.forward()
    def forward(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.backend,timeout=4)
        headers=dict(self.headers)
        headers['Tailscale-User-Login']=headers.pop('X-Test-Login',ENGINEER)
        body=self.rfile.read(int(self.headers.get('Content-Length',0))) if self.command=='POST' else None
        try:
            connection.request(self.command,self.path,body,headers)
            response=connection.getresponse();self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in ('server','date','connection'):self.send_header(k,v)
            self.send_header('Connection','close');self.end_headers()
            if response.headers.get('Content-Type','').startswith('text/event-stream'):
                while not self.server.finished.is_set():
                    line=response.readline()
                    if not line:break
                    if self.server.pause_stream.is_set():continue
                    self.wfile.write(line);self.wfile.flush()
            else:self.wfile.write(response.read())
        except (OSError,TimeoutError):pass
        finally:connection.close();self.close_connection=True


def tls_context(directory):
    key=ec.generate_private_key(ec.SECP256R1())
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'stint-test.example.ts.net')])
    now=datetime.datetime.now(datetime.timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
          .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('stint-test.example.ts.net')]),False)
          .sign(key,hashes.SHA256()))
    cert_path,key_path=Path(directory)/'cert.pem',Path(directory)/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert_path,key_path)
    return context


def main():
 class FakeCodex:
  def status(self,*args,**kwargs):return {'available':False,'connected':False,'login_pending':False}
  def close(self):pass
 with tempfile.TemporaryDirectory() as directory,patch.object(server,'DATA',Path(directory)/'data'),patch.object(server,'CODEX',FakeCodex()):
  sdk=BrowserSDK();local=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler,live_source=sdk,remote_port=0)
  local.live.start();threading.Thread(target=local.serve_forever,daemon=True).start()
  local.remote.configure({'enabled':True,'origin':ORIGIN,'users':[{'login':ENGINEER,'role':'engineer','name':'Race engineer'},
                          {'login':'viewer@example.com','role':'viewer','name':'Team viewer'}]})
  proxy=ThreadingHTTPServer(('127.0.0.1',8443),IdentityProxy);proxy.daemon_threads=True
  proxy.backend=local.remote.http.server_port;proxy.finished=threading.Event();proxy.pause_stream=threading.Event()
  proxy.socket=tls_context(directory).wrap_socket(proxy.socket,server_side=True)
  threading.Thread(target=proxy.serve_forever,daemon=True).start()
  output=Path('test-results');output.mkdir(exist_ok=True)
  errors=[]
  try:
   with sync_playwright() as p:
    browser=p.chromium.launch(channel='msedge',headless=True,args=['--host-resolver-rules=MAP stint-test.example.ts.net 127.0.0.1','--no-proxy-server'])
    engineer=browser.new_context(ignore_https_errors=True,viewport={'width':1920,'height':1080})
    page=engineer.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(ORIGIN);expect(page.locator('#remote-identity')).to_contain_text('Engineer')
    expect(page.locator('#live-connection')).to_contain_text('Live connected')
    with local.live.lock:
     local.live.laps.append({'number':1,'duration':60,'fuel_used':1.15,'eligible':True,'reasons':[]})
     local.live.fuel_totals=totals_from(local.live.laps);local.live.strategy=None
    expect(page.locator('[data-fuel-basis="last_lap"] td').first).to_have_text('1.150')
    expect(page.locator('#live-pit-apply')).to_be_disabled()
    viewer=browser.new_context(ignore_https_errors=True,extra_http_headers={'X-Test-Login':'viewer@example.com'},viewport={'width':1366,'height':768})
    view=viewer.new_page();view.on('pageerror',lambda e:errors.append(str(e)))
    view.goto(ORIGIN);expect(view.locator('#remote-identity')).to_contain_text('Viewer')
    view.locator('#live-view-strategy').click();expect(view.locator('#live-save-plan')).to_be_disabled()
    expect(view.locator('[data-plan-field="fuel_basis"]')).to_be_disabled()
    local.live.pit.authorize(True,ENGINEER)
    expect(page.locator('#live-pit-apply')).to_be_enabled()
    for c in sdk.pressures:expect(page.locator('#live-pit-pressure-was-'+c)).to_be_hidden()
    page.locator('#live-pit-pressure-lf').fill('162')
    expect(page.locator('#live-pit-pressure-was-lf')).to_have_text('Was 159 kPa')
    expect(page.locator('#live-pit-pressure-was-lf')).to_be_visible()
    expect(page.locator('#live-pit-pressure-lf')).to_have_css('color','rgb(0, 128, 255)')
    page.locator('.pit-fieldset').nth(1).screenshot(path=str(output/'pit-pressure-was.png'))
    page.locator('#live-pit-pressure-lf').fill('159')
    expect(page.locator('#live-pit-pressure-was-lf')).to_be_hidden()
    page.locator('#live-pit-fuel').fill('42');page.locator('#live-pit-lf').check();page.locator('#live-pit-rf').check()
    for c,value in [('lf','159'),('rf','160'),('lr','161'),('rr','162')]:page.locator('#live-pit-pressure-'+c).fill(value)
    page.locator('#live-pit-compound').select_option('1')
    page.locator('#live-pit-tear-off').uncheck();page.locator('#live-pit-fast-repair').uncheck()
    page.locator('#live-pit-apply').click();expect(page.locator('#live-pit-result')).to_contain_text('Confirmed:',timeout=4000)
    expect(page.locator('#live-pit-actual')).to_contain_text('42.00 L')
    expect(page.locator('#live-pit-actual')).to_contain_text('LF 159')
    expect(page.locator('#live-pit-actual')).to_contain_text('Wet')
    expect(page.locator('#live-pit-result')).to_contain_text('Private confirmation sent')
    assert sdk.messages[-1][1]=='Race engineer'
    assert sdk.flags==16|15 and set(sdk.thread_ids)=={local.live.thread.ident}
    assert sdk.pressures=={'lf':159,'rf':160,'lr':161,'rr':162} and sdk.compound==1
    expect(page.locator('#live-pit-pressure-was-rf')).to_have_text('Was 159 kPa')
    expect(page.locator('#live-pit-pressure-was-rf')).to_be_visible()
    sdk.fitted_pressures['rf']=160
    expect(page.locator('#live-pit-pressure-was-rf')).to_be_hidden()
    expect(view.locator('#live-pit-apply')).to_be_disabled()
    for id in ['live-pit-margin','live-pit-pressure-lf','live-pit-compound','live-pit-tear-off','live-pit-fast-repair']:expect(view.locator('#'+id)).to_be_disabled()
    page.screenshot(path=str(output/'remote-pitwall-1920.png'))
    page.set_viewport_size({'width':1366,'height':768});page.screenshot(path=str(output/'remote-pitwall-1366.png'))
    page.locator('#live-view-strategy').click();page.locator('[data-plan-field="race_laps"]').fill('25')
    local.live.update_plan({'race_laps':30},local.live.record['id'],0)
    expect(page.locator('#live-plan-status')).to_contain_text('changed on another browser')
    page.locator('#live-save-plan').click();expect(page.locator('#live-plan-status')).to_contain_text('changed on another browser')
    page.locator('#live-reload-plan').click();expect(page.locator('[data-plan-field="race_laps"]')).to_have_value('30')
    page.locator('[data-plan-field="pace_seconds"]').fill('60');page.locator('[data-plan-field="burn_litres"]').fill('2')
    page.locator('[data-plan-field="pit_loss_seconds"]').fill('20');page.locator('[data-plan-field="refuel_lps"]').fill('2')
    page.locator('[data-plan-field="tire_seconds"]').fill('10');page.locator('[data-plan-field="driver_change_seconds"]').fill('0')
    page.locator('#live-save-plan').click();expect(page.locator('#live-plan-status')).to_contain_text('Saved on racing PC')
    page.locator('[data-scenario="auto-long"] [data-use]').click();expect(page.locator('#live-plan-status')).to_contain_text('Saved on racing PC')
    page.locator('#live-view-pitwall').click();page.locator('#live-pit-load').click()
    expect(page.locator('#live-pit-draft-note')).to_contain_text('rounded up')
    planned_fuel=page.locator('#live-pit-fuel').input_value()
    sequence=page.evaluate('LivePitwall.getSnapshot().sequence')
    page.wait_for_function('sequence=>LivePitwall.getSnapshot().sequence>sequence+8',arg=sequence)
    expect(page.locator('#live-pit-draft-note')).to_contain_text('rounded up')
    expect(page.locator('#live-pit-fuel')).to_have_value(planned_fuel)
    page.locator('#live-pit-fuel').fill('38')
    sequence=page.evaluate('LivePitwall.getSnapshot().sequence')
    page.wait_for_function('sequence=>LivePitwall.getSnapshot().sequence>sequence+8',arg=sequence)
    expect(page.locator('#live-pit-fuel')).to_have_value('38')
    page.locator('#live-pit-sync').click()
    sdk.fuel=7
    expect(page.locator('#live-pit-result')).to_contain_text('have since changed')
    before=len(sdk.calls);proxy.pause_stream.set();page.wait_for_timeout(2700)
    expect(page.locator('#live-pit-apply')).to_be_disabled();expect(page.locator('#live-connection')).to_contain_text('stale')
    proxy.pause_stream.clear();expect(page.locator('#live-connection')).to_contain_text('Live connected')
    assert len(sdk.calls)==before
    local.live.pit.authorize(False,ENGINEER);expect(page.locator('#live-pit-apply')).to_be_disabled()
    page.locator('#live-view-history').click();expect(page.locator('#live-history-content')).to_contain_text('Pit request confirmed')
    page.set_viewport_size({'width':390,'height':844});page.locator('#live-view-pitwall').click()
    page.screenshot(path=str(output/'remote-pitwall-mobile.png'))
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    local_page=browser.new_page(viewport={'width':1366,'height':768})
    local_page.on('pageerror',lambda e:errors.append(str(e)))
    local_page.goto(f'http://127.0.0.1:{local.server_port}')
    local_page.locator('#live-remote-settings').click();expect(local_page.locator('#remote-settings-dialog')).to_be_visible()
    expect(local_page.locator('#remote-users input[data-key="login"]').first).to_have_value(ENGINEER)
    local_page.screenshot(path=str(output/'remote-settings.png'))
    local.remote.configure({**local.remote.settings,'users':[{'login':ENGINEER,'role':'viewer'}]})
    expect(page.locator('#remote-identity')).to_contain_text('Viewer',timeout=8000)
    page.locator('#live-view-strategy').click();expect(page.locator('#live-save-plan')).to_be_disabled()
    assert not errors,errors
    browser.close()
    print('Remote browser acceptance passed: HTTPS identity, viewer permissions, engineer edits, conflicts, collector commands/readback, service preservation, planned-stop draft, stale/reconnect/revoke, history, desktop/mobile, local settings.',flush=True)
  finally:
   proxy.finished.set();proxy.shutdown();proxy.server_close();local.shutdown();local.server_close()


if __name__=='__main__':main()
