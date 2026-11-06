"""Exercise the setup helper with a fake CLI; never change this PC's Tailscale."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote import RemoteGateway

ROOT=Path(__file__).resolve().parents[1]
TARGET='http://127.0.0.1:8766'
HOST='racing-pc.example.ts.net:8443'


@unittest.skipUnless(os.name=='nt','Windows setup helper')
class SetupHelperTests(unittest.TestCase):
    def run_helper(self,serve,disable=False,state='Running'):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);log=root/'calls.txt'
            (root/'status.json').write_text(json.dumps({'BackendState':state,'Self':{'DNSName':'racing-pc.example.ts.net.'}}))
            (root/'serve.json').write_text(json.dumps(serve))
            fake=root/'tailscale.ps1'
            fake.write_text("""$global:LASTEXITCODE=0
if ($args[0] -eq 'status') { Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'status.json'); return }
if ($args[0] -eq 'serve' -and $args[1] -eq 'status') { Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'serve.json'); return }
($args -join ' ') | Add-Content -LiteralPath (Join-Path $PSScriptRoot 'calls.txt')
""")
            # Resolve only our fake CLI. The helper still executes its own checks.
            wrapper=root/'run.ps1'
            helper=str(ROOT/'Setup Remote Pitwall.ps1').replace("'","''")
            wrapper.write_text("function Get-Command { param($Name,$ErrorAction) [pscustomobject]@{ Source=(Join-Path $PSScriptRoot 'tailscale.ps1') } }\n"
                               f"& '{helper}' {'-Disable' if disable else ''}\n")
            result=subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',str(wrapper)],
                                  capture_output=True,text=True,timeout=10)
            calls=log.read_text().splitlines() if log.exists() else []
            return result,calls

    def test_adds_only_own_endpoint_preserving_unrelated_serve(self):
        unrelated={'Web':{'other.example.ts.net:443':{'Handlers':{'/':{'Proxy':'http://127.0.0.1:9000'}}}},
                   'TCP':{'443':{'HTTPS':True}},'AllowFunnel':{'other.example.ts.net:443':True}}
        result,calls=self.run_helper(unrelated)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(calls,[f'serve --bg --https=8443 {TARGET}'])
        self.assertIn('https://racing-pc.example.ts.net:8443',result.stdout)

    def test_refuses_port_conflicts_and_public_pitwall(self):
        for serve in [
            {'Web':{HOST:{'Handlers':{'/':{'Proxy':'http://127.0.0.1:9999'}}}}},
            {'Web':{HOST:{'Handlers':{'/':{'Proxy':TARGET},'/other':{'Proxy':TARGET}}}}},
            {'TCP':{'8443':{'TCPForward':'localhost:9999'}}},
            {'AllowFunnel':{HOST:True}},
        ]:
            with self.subTest(serve=serve):
                result,calls=self.run_helper(serve)
                self.assertNotEqual(result.returncode,0);self.assertEqual(calls,[])
                self.assertIn('Nothing was changed',result.stderr)

    def test_disable_only_matches_owned_endpoint_and_requires_signin(self):
        owned={'Web':{HOST:{'Handlers':{'/':{'Proxy':TARGET}}}},'TCP':{'8443':{'HTTPS':True}}}
        result,calls=self.run_helper(owned,disable=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(calls,['serve --https=8443 off'])
        result,calls=self.run_helper({},disable=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(calls,[])
        result,calls=self.run_helper({},state='NeedsLogin')
        self.assertNotEqual(result.returncode,0);self.assertEqual(calls,[])


class TransportStatusTests(unittest.TestCase):
    def test_exact_private_endpoint_and_unrelated_funnel(self):
        with tempfile.TemporaryDirectory() as directory:
            gateway=RemoteGateway(directory,ROOT,None)
            responses=[subprocess.CompletedProcess([],0,json.dumps({'BackendState':'Running','Self':{'DNSName':'racing-pc.example.ts.net.'}})),
                       subprocess.CompletedProcess([],0,json.dumps({'Web':{HOST:{'Handlers':{'/':{'Proxy':TARGET}}}},
                         'AllowFunnel':{'other.example.ts.net:443':True}}))]
            with patch('remote.shutil.which',return_value='tailscale'),patch('remote.subprocess.run',side_effect=responses):
                status=gateway.transport()
            self.assertTrue(status['signed_in']);self.assertTrue(status['serve_configured'])
            self.assertFalse(status['public_funnel']);self.assertEqual(status['origin'],'https://racing-pc.example.ts.net:8443')
            self.assertEqual(gateway.transport(),status)


if __name__=='__main__':unittest.main()
