"""Windows exclusive service launcher; all paths are relative to this file."""
import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
APP_ID = 'personal-job-workbench'


class DailyLog(logging.Handler):
    def __init__(self, logs):
        super().__init__()
        self.logs = logs

    def emit(self, record):
        today = datetime.now(timezone(timedelta(hours=8))).date()
        with (self.logs / f'app-{today:%Y%m%d}.log').open('a',encoding='utf-8') as out:
            out.write(self.format(record)+'\n')
        for path in self.logs.glob('app-????????.log'):
            try:
                day = datetime.strptime(path.stem[4:],'%Y%m%d').date()
                if (today-day).days>=14:
                    path.unlink()
            except (ValueError,OSError):
                pass


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=1) as r:
            return json.load(r)
    except Exception:
        return {}


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--no-browser', action='store_true')
    args.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    opt = args.parse_args()
    run = opt.data_dir.resolve().parent / ('run' if opt.data_dir.resolve() == ROOT / 'data' else opt.data_dir.name + '-run')
    logs = ROOT / 'logs'
    run.mkdir(parents=True, exist_ok=True)
    logs.mkdir(exist_ok=True)
    handler = DailyLog(logs)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])
    # Lock before database initialization; an OS lock is released on process exit.
    lock_file = open(run / 'service.lock', 'a+b', buffering=0)
    if os.fstat(lock_file.fileno()).st_size == 0:
        try:
            lock_file.write(b'0')
        except OSError:
            pass  # Another launcher already owns the byte-range lock.
    lock_file.seek(0)
    import msvcrt
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        import tomllib
        for _ in range(30):
            try:
                config = tomllib.loads((opt.data_dir / 'config.local.toml').read_text('utf-8'))
                port = int((run / 'port.txt').read_text())
                status = health(port)
                info = json.loads((run / 'service.json').read_text())
                if status.get('app_id') == APP_ID and status.get('workspace_id') == config['workspace_id'] and status.get('pid') == info['pid'] and status.get('started_at') == info['started_at']:
                    print(f'复用已有工作台：http://127.0.0.1:{port}')
                    if not opt.no_browser:
                        webbrowser.open(f'http://127.0.0.1:{port}')
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError('工作台启动锁正被占用，但服务身份尚未确认；请查看日志后重试。未终止其他进程。')
    from workbench.web import create_app
    app = create_app(opt.data_dir)
    store = app.extensions['store']
    from waitress import create_server
    server = None
    for port in range(int(store.config['port']), int(store.config['port']) + 100):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((store.config['host'], port))
            server = create_server(app, sockets=[listener], threads=4, max_request_body_size=2**63-1)
            break
        except OSError:
            listener.close()
            status = health(port)
            if status.get('app_id') == APP_ID and status.get('workspace_id') == store.workspace_id:
                if not opt.no_browser:
                    webbrowser.open(f'http://127.0.0.1:{port}')
                return
            logging.info('端口 %s 被其他服务或工作区占用，尝试下一端口', port)
    if server is None:
        raise RuntimeError('连续 100 个端口不可用')
    (run / 'port.txt').write_text(str(port))
    ownership=json.dumps(dict(pid=os.getpid(), started_at=app.config['STARTED_AT']))
    (run / 'service.json').write_text(ownership)
    lock_file.seek(1)
    lock_file.write(ownership.encode('utf-8'))
    lock_file.truncate()
    logging.info('启动 %s Python=%s SQLite=%s journal=DELETE workspace=%s port=%s', app.config['APP_VERSION'], sys.version, __import__('sqlite3').sqlite_version, store.workspace_id, port)
    if not opt.no_browser:
        import threading
        threading.Timer(.7, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    try:
        server.run()
    finally:
        server.close()
        lock_file.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (ROOT / 'logs').mkdir(exist_ok=True)
        import traceback
        error = traceback.format_exc()
        (ROOT / 'logs' / 'startup-error.log').write_text(error, 'utf-8')
        print(error, file=sys.stderr)
        sys.exit(1)
