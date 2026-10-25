"""Portable Windows entry points; data stays beside this file, never in runtime."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tomllib
import uuid
import webbrowser

from launcher import APP_ID, ROOT, health, service_paths


def executable(windowless=False):
    name = 'pythonw.exe' if windowless else 'python.exe'
    choices = [ROOT/'runtime'/name, ROOT/'.venv/Scripts'/name]
    for path in choices:
        if path.is_file():
            return path
    raise RuntimeError('运行环境缺失，请完整解压分享包，或按 README 安装源码依赖。')


def status(data_dir):
    data_dir = Path(data_dir).resolve()
    config = data_dir/'config.local.toml'
    run = service_paths(data_dir)
    if not config.exists() or not (run/'port.txt').exists():
        return None
    try:
        workspace = tomllib.loads(config.read_text('utf-8'))['workspace_id']
        port = int((run/'port.txt').read_text())
        if not 1 <= port <= 65535:
            raise ValueError()
        actual = health(port)
        if not actual:
            return None
        owner = json.loads((run/'service.json').read_text('utf-8'))
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError('无法确认当前工作区的服务记录，请保留数据并查看日志。') from exc
    if (actual.get('app_id') != APP_ID or actual.get('workspace_id') != workspace
            or actual.get('pid') != owner.get('pid')
            or actual.get('started_at') != owner.get('started_at')):
        raise RuntimeError('端口或服务身份与本工作区不符，未打开或关闭其他程序。')
    return dict(actual, url=f'http://127.0.0.1:{port}', port=port)


def start(data_dir, no_browser=False):
    data_dir = Path(data_dir).resolve()
    try:
        current = status(data_dir)
    except RuntimeError:
        # The exclusive launcher handles an occupied port safely, without opening it.
        current = None
    if not current:
        logs = ROOT/'logs'
        logs.mkdir(exist_ok=True)
        with (logs/'portable-startup.log').open('ab') as log:
            child = subprocess.Popen([str(executable()), '-X', 'utf8', str(ROOT/'launcher.py'),
                                      '--no-browser', '--data-dir', str(data_dir)], cwd=ROOT,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic()+35
        while time.monotonic() < deadline:
            try:
                current = status(data_dir)
            except RuntimeError:
                current = None
            if current:
                break
            if child.poll() not in (None, 0):
                raise RuntimeError('工作台未能启动，请查看 logs/portable-startup.log 和 startup-error.log。')
            time.sleep(.15)
        if not current:
            raise RuntimeError('启动尚未完成，请稍后重试；未终止任何进程，详细情况见 logs。')
    if not no_browser:
        if not webbrowser.open(current['url']):
            raise RuntimeError('工作台已启动，请手动打开：'+current['url'])
    return current


def stop(data_dir):
    current = status(data_dir)
    if not current:
        return dict(message='本工作区尚未运行。')
    run = service_paths(data_dir)
    request = {key:current[key] for key in ('pid','started_at','workspace_id')}
    temporary = run/('stop-'+uuid.uuid4().hex+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(request, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary,run/'stop-request.json')
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        actual=health(current['port'])
        if not actual or any(actual.get(key)!=current[key] for key in ('pid','started_at','workspace_id')):
            return dict(message='工作台服务已关闭；数据已保留。')
        time.sleep(.15)
    raise RuntimeError('正在等待保存或备份结束，请稍后再点关闭；没有强制结束进程。')


def create_shortcut(desktop_dir=None):
    # Resolve the real Desktop (including OneDrive redirection) via Windows.
    script = r'''
$ErrorActionPreference = 'Stop'
$targetDesktop = if ($env:JOB_WORKBENCH_TEST_DESKTOP) { $env:JOB_WORKBENCH_TEST_DESKTOP } else { [Environment]::GetFolderPath('Desktop') }
if (-not (Test-Path -LiteralPath $targetDesktop -PathType Container)) { throw '桌面目录不存在' }
$shortcutFile = Join-Path $targetDesktop '个人求职工作台.lnk'
if (Test-Path -LiteralPath $shortcutFile) { throw '桌面已有同名快捷方式，未覆盖；请先重命名旧快捷方式' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutFile)
$shortcut.TargetPath = $env:JOB_WORKBENCH_PYTHONW
$shortcut.Arguments = '-X utf8 "' + $env:JOB_WORKBENCH_ENTRY + '" start --gui'
$shortcut.WorkingDirectory = $env:JOB_WORKBENCH_ROOT
$shortcut.IconLocation = (Join-Path $env:JOB_WORKBENCH_ROOT 'workbench\static\workbench-gpt-a.ico') + ',0'
$shortcut.Description = '启动本机个人求职工作台'
$shortcut.Save()
[Console]::WriteLine($shortcutFile)
'''
    env = dict(os.environ, JOB_WORKBENCH_PYTHONW=str(executable(True)),
               JOB_WORKBENCH_ENTRY=str(ROOT/'portable.py'), JOB_WORKBENCH_ROOT=str(ROOT),
               JOB_WORKBENCH_TEST_DESKTOP=str(Path(desktop_dir).resolve()) if desktop_dir else '')
    # UTF-16 EncodedCommand preserves Chinese independent of the shell code page.
    import base64
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                          env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          creationflags=subprocess.CREATE_NO_WINDOW,timeout=20)
    if result.returncode:
        raise RuntimeError('快捷方式未创建：桌面已有同名入口、目录不可写或 Windows 脚本组件不可用。原入口未覆盖。')
    return dict(message='桌面快捷方式已创建，可双击打开。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['start','stop','shortcut','status'])
    parser.add_argument('--data-dir',type=Path,default=ROOT/'data')
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--gui',action='store_true')
    parser.add_argument('--desktop-dir',type=Path,help=argparse.SUPPRESS)
    opt=parser.parse_args()
    try:
        if opt.command=='start':
            result=start(opt.data_dir,opt.no_browser)
        elif opt.command=='stop':
            result=stop(opt.data_dir)
        elif opt.command=='shortcut':
            result=create_shortcut(opt.desktop_dir)
        else:
            result=status(opt.data_dir) or dict(message='本工作区尚未运行。')
        if opt.gui:
            if opt.command!='start':ctypes.windll.user32.MessageBoxW(None,result.get('message','操作完成'),'个人求职工作台',0x40)
        else:
            print(json.dumps(result,ensure_ascii=False))
        return 0
    except Exception as exc:
        logs=ROOT/'logs'
        import traceback
        try:
            logs.mkdir(exist_ok=True)
            (logs/'portable-error.log').write_text(traceback.format_exc(),'utf-8')
            message=str(exc)+'\n\n错误记录：'+str(logs/'portable-error.log')
        except OSError:
            message=str(exc)+'\n\n当前目录无法写入日志，请将完整分享包解压到自己的可写文件夹后重试。'
        if opt.gui:ctypes.windll.user32.MessageBoxW(None,message,'工作台操作未完成',0x10)
        else:print(message,file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
