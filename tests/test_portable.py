import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import launcher
import portable
from workbench.storage import replace_generation
from scripts.build_portable import prune_entrypoints


def test_stop_request_requires_exact_workspace_process_and_start(tmp_path):
    path=tmp_path/'stop.json'
    identity=dict(pid=100,started_at='start',workspace_id='workspace')
    assert not launcher.stop_requested(path,identity)
    path.write_text('{broken','utf-8')
    assert not launcher.stop_requested(path,identity)
    for key in identity:
        path.write_text(json.dumps(dict(identity,**{key:'other'})),'utf-8')
        assert not launcher.stop_requested(path,identity)
    path.write_text(json.dumps(identity),'utf-8')
    assert launcher.stop_requested(path,identity)


def test_shutdown_waits_for_coordinated_save(tmp_path):
    identity=dict(pid=100,started_at='start',workspace_id='workspace')
    path=tmp_path/'stop-request.json';path.write_text(json.dumps(identity),'utf-8')
    events=[];lock=threading.RLock();lock.acquire()
    store=SimpleNamespace(lock=lock,maintenance=False)
    server=SimpleNamespace(_map={},close=lambda:events.append('closed'),
        task_dispatcher=SimpleNamespace(shutdown=lambda **kw:events.append('workers_stopped')),
        asyncore=SimpleNamespace(loop=lambda **kw:time.sleep(.01),close_all=lambda **kw:events.append('channels_closed')))
    worker=threading.Thread(target=launcher.serve_until_stopped,args=(server,store,tmp_path,identity))
    worker.start()
    try:
        time.sleep(.05)
        assert not events and worker.is_alive() and not store.maintenance
    finally:lock.release()
    worker.join(timeout=3)
    assert not worker.is_alive() and store.maintenance
    assert events==['closed','workers_stopped','channels_closed'] and not path.exists()


def test_status_rejects_other_workspace_and_stale_process(tmp_path):
    data=tmp_path/'数据';data.mkdir()
    (data/'config.local.toml').write_text('workspace_id="test-workspace"','utf-8')
    run=launcher.service_paths(data);run.mkdir()
    (run/'port.txt').write_text('9000')
    (run/'service.json').write_text(json.dumps(dict(pid=100,started_at='now')),'utf-8')
    valid=dict(app_id=launcher.APP_ID,workspace_id='test-workspace',pid=100,started_at='now')
    with patch('portable.health',return_value=valid):assert portable.status(data)['port']==9000
    for changed in [dict(workspace_id='other'),dict(pid=101),dict(started_at='old'),dict(app_id='other')]:
        with patch('portable.health',return_value=dict(valid,**changed)):
            with pytest.raises(RuntimeError,match='身份'):portable.stop(data)
    assert not (run/'stop-request.json').exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows shortcuts')
def test_shortcut_created_in_isolated_desktop_without_overwrite(tmp_path):
    assert portable.create_shortcut(tmp_path)['message']
    path=tmp_path/'个人求职工作台.lnk'
    assert path.exists() and path.stat().st_size>100
    before=path.read_bytes()
    with pytest.raises(RuntimeError,match='未创建'):portable.create_shortcut(tmp_path)
    assert path.read_bytes()==before


@pytest.mark.skipif(os.name!='nt',reason='Windows directory sharing')
def test_restore_generation_retries_only_temporary_windows_lock():
    locked=PermissionError('临时占用');locked.winerror=5
    with patch('workbench.storage.os.replace',side_effect=[locked,None]) as rename:
        replace_generation('source','target')
    assert rename.call_count==2
    with patch('workbench.storage.os.replace',side_effect=locked) as rename, patch('workbench.storage.time.monotonic',side_effect=[0,10]):
        with pytest.raises(PermissionError):replace_generation('source','target')
    assert rename.call_count==1


def test_restore_generation_preserves_real_io_failure():
    with patch('workbench.storage.os.replace',side_effect=OSError('disk failure')) as rename:
        with pytest.raises(OSError,match='disk failure'):replace_generation('source','target')
    assert rename.call_count==1


def test_portable_error_still_reports_when_log_directory_is_unwritable(tmp_path,capsys):
    blocked=tmp_path/'blocked';blocked.write_text('file, not a directory')
    with patch('portable.ROOT',blocked), patch('portable.sys.argv',['portable.py','start','--no-browser']), patch('portable.start',side_effect=RuntimeError('模拟启动失败')):
        assert portable.main()==1
    message=capsys.readouterr().err
    assert '模拟启动失败' in message and '可写文件夹' in message


def test_build_omits_path_bound_cli_wrappers_and_preserves_unknown_files(tmp_path):
    entrypoints=tmp_path/'bin';entrypoints.mkdir()
    for name in ('flask.exe','markdown_py.exe','waitress-serve.exe'):
        (entrypoints/name).write_bytes(b'build-path-bound wrapper')
    prune_entrypoints(tmp_path)
    assert not entrypoints.exists()
    entrypoints.mkdir();other=entrypoints/'unexpected.exe';other.write_bytes(b'keep')
    with pytest.raises(ValueError,match='未审查'):prune_entrypoints(tmp_path)
    assert other.read_bytes()==b'keep'
