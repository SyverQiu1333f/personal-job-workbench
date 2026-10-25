import json
import os
import socket
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import pytest
from workbench.storage import Store

ROOT=Path(__file__).resolve().parent.parent


def read_health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz',timeout=.5) as f:
            return json.load(f)
    except Exception:return None


def service_snapshot(run, start_port, workspace_id):
    """Read records AND actual listening services; a second port must not hide."""
    port_text=(run/'port.txt').read_text()
    owner=json.loads((run/'service.json').read_text())
    port=int(port_text)
    result=subprocess.run(['netstat','-ano','-p','tcp'],capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode==0, result.stderr
    candidates={port}
    for line in result.stdout.splitlines():
        parts=line.split()
        if len(parts)>=5 and parts[0]=='TCP' and parts[-2]=='LISTENING':
            candidate=int(parts[1].rsplit(':',1)[1])
            if start_port<=candidate<min(start_port+100,65536):candidates.add(candidate)
    services=[]
    for candidate in sorted(candidates):
        health=read_health(candidate)
        if health and health.get('app_id')=='personal-job-workbench' and health.get('workspace_id')==workspace_id:
            services.append(dict(port=candidate,pid=health['pid'],started_at=health['started_at']))
    return dict(port_text=port_text,owner=owner,health=read_health(port),services=services)


def assert_reused(before,after):
    assert after['port_text']==before['port_text']
    assert after['owner']==before['owner']
    for key in ['app_id','workspace_id','pid','started_at']:
        assert after['health'][key]==before['health'][key]
    assert len(before['services'])==len(after['services'])==1
    assert after['services']==before['services']


@pytest.mark.skipif(os.name!='nt',reason='Windows launcher OS lock')
@pytest.mark.parametrize('occupant',['other-program','other-workspace'])
def test_duplicate_start_and_port_identity(tmp_path,occupant):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload=json.dumps(dict(app_id='personal-job-workbench' if occupant=='other-workspace' else 'other-app',workspace_id='other-workspace')).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*a):pass
    occupied=HTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=occupied.serve_forever,daemon=True).start()
    root=tmp_path/'中文工作区'
    store=Store(root)
    config=root/'config.local.toml'
    config.write_text(config.read_text('utf-8').replace('port = 8765',f'port = {occupied.server_port}'),'utf-8')
    cmd=[os.environ['COMSPEC'],'/c',str(ROOT/'启动工作台.bat'),'--no-browser','--data-dir',str(root)]
    # Only terminate the exact child process created by this test.
    process=subprocess.Popen(cmd,cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        run=root.parent/(root.name+'-run')
        deadline=time.monotonic()+15
        status=None
        while time.monotonic()<deadline:
            if (run/'port.txt').exists():
                port=int((run/'port.txt').read_text())
                status=read_health(port)
                if status:break
            time.sleep(.1)
        assert status and port!=occupied.server_port
        server_pid=status['pid']
        assert status['workspace_id']==store.workspace_id and server_pid==json.loads((run/'service.json').read_text())['pid']
        before=service_snapshot(run,occupied.server_port,store.workspace_id)
        second=subprocess.run(cmd,cwd=tmp_path,capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        assert second.returncode==0
        assert read_health(port)['pid']==server_pid
        after=service_snapshot(run,occupied.server_port,store.workspace_id)
        assert_reused(before,after)
        print(json.dumps(dict(occupant=occupant,before=before,after=after,reuse_output=second.stdout.decode('utf-8','replace')),ensure_ascii=True))
        # A real extra health endpoint impersonates a wrongly launched second instance.
        # Production code stays untouched; unchanged port.txt and owner alone cannot pass.
        class ExtraHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload=json.dumps(dict(status,pid=server_pid+100000,started_at='second-instance')).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            def log_message(self,*a):pass
        extra=None
        for candidate in range(port+1,min(occupied.server_port+100,65536)):
            try:extra=HTTPServer(('127.0.0.1',candidate),ExtraHandler);break
            except OSError:continue
        assert extra is not None
        threading.Thread(target=extra.serve_forever,daemon=True).start()
        try:
            bad=service_snapshot(run,occupied.server_port,store.workspace_id)
            with pytest.raises(AssertionError):assert_reused(before,bad)
        finally:
            extra.shutdown();extra.server_close()
        assert occupied.fileno()!=-1
    finally:
        if status and status.get('workspace_id')==store.workspace_id and status.get('pid'):
            os.kill(status['pid'],signal.SIGTERM)
        if process.poll() is None:process.terminate()
        process.communicate(timeout=10)
        occupied.shutdown();occupied.server_close()
    # OS lock from dead process is automatically released; record files may remain.
    restarted=subprocess.Popen(cmd,cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            port=int((run/'port.txt').read_text())
            status=read_health(port)
            if status and status.get('workspace_id')==store.workspace_id and status.get('pid')!=server_pid:break
            time.sleep(.1)
        assert status and status['pid']!=server_pid
    finally:
        if status and status.get('workspace_id')==store.workspace_id and status.get('pid'):
            os.kill(status['pid'],signal.SIGTERM)
        if restarted.poll() is None:restarted.terminate()
        restarted.communicate(timeout=10)
