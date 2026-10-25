"""Reproducible synthetic benchmark, never writes the live workspace."""
import json
import platform
import statistics
import tempfile
import time
import io
import os
import argparse
from pathlib import Path
from werkzeug.datastructures import FileStorage
from workbench import db
from workbench.web import create_app
from workbench.reporting import export_analysis


def run():
    with tempfile.TemporaryDirectory(prefix='求职规模验证-') as directory:
        app=create_app(Path(directory)/'数据')
        store=app.extensions['store'];c=db.Database(store.path)
        file_ids=[]
        for size in [10_000_000,15_000_000]:
            # Synthetic storage workload only; real rendering uses the valid PDF fixtures.
            raw=b'%PDF-1.4\n'+os.urandom(size-9)
            info,_=store.put(FileStorage(stream=io.BytesIO(raw),filename=f'吞吐样例-{size}.pdf'),True)
            file_ids.append(c.insert('files',info))
        for n in range(1000):
            jid=c.insert('jobs',dict(company=f'测试公司{n}',title=f'测试岗位{n}',stage=db.STAGES[n%6],star=int(n%10==0),created_at=db.now(),updated_at=db.now()))
            c.set_tags(jid, '产品,宁波' if n%2 else '产品,技术')
            for p in range(5):
                c.insert('platforms',dict(job_id=jid,platform=f'平台{p}',communicated=1,communication_date='2026-10-01',reply='已有回复' if p%2 else '待核对',invitation='已有明确邀约' if p==1 else '待核对',created_at=db.now(),updated_at=db.now()))
                mid=c.insert('materials',dict(title=f'测试资料{n}-{p}',kind='背调',body='样例正文\n'*20,file_id=file_ids[p%2],original_name='合成吞吐原件.pdf',created_at=db.now(),updated_at=db.now()))
                c.insert('job_materials',dict(job_id=jid,material_id=mid))
        c.c.commit()
        def timed(action,repeat=15):
            times=[]
            for _ in range(repeat):
                start=time.perf_counter();action();times.append((time.perf_counter()-start)*1000)
            return dict(first_ms=round(times[0],2),warm_median_ms=round(statistics.median(times[1:]),2),max_ms=round(max(times),2))
        queries=timed(lambda:c.jobs({'tags':'产品,宁波','tag_mode':'all','platform':'平台1','reply':'已有回复','page':'2'}))
        client=app.test_client()
        def request(path):
            response=client.get(path)
            assert response.status_code==200
            response.get_data();response.close()
        home=timed(lambda:request('/'))
        # Prepare the daily snapshot and preference separately from repeated page timing.
        request('/jobs?tags=产品&platform=平台1&page=2')
        listing=timed(lambda:request('/jobs?tags=产品&platform=平台1&page=2'))
        analysis=timed(lambda:request('/analysis'))
        export_bytes=[]
        def export():
            with io.BytesIO() as out:
                manifest=export_analysis(c,store,{'archived':'all'},out)
                assert manifest['unique_material_count']==5000 and len(manifest['files'])==2
                export_bytes.append(out.tell())
        exporting=timed(export,3)
        backups=[]
        backup_timing=timed(lambda:backups.append(store.backup('manual')),3)
        zipped=Path(directory)/'完整备份.zip'
        start=time.perf_counter();store.zip_backup(backups[-1],zipped);zip_ms=(time.perf_counter()-start)*1000
        restored=__import__('workbench.storage',fromlist=['Store']).Store(Path(directory)/'独立恢复')
        start=time.perf_counter();restored.restore(restored.unpack(zipped));restore_ms=(time.perf_counter()-start)*1000
        check=db.Database(restored.path)
        counts={t:check.one('SELECT count(*) n FROM '+t)['n'] for t in ['jobs','platforms','materials']}
        assert counts=={'jobs':1000,'platforms':5000,'materials':5000};check.c.close()
        result=dict(app_version=db.VERSION,machine=platform.platform(),processor=platform.processor(),python=platform.python_version(),sqlite=__import__('sqlite3').sqlite_version,journal='DELETE',jobs=1000,platform_records=5000,materials=5000,material_body_chars=5000*len('样例正文\n'*20),unique_originals=2,original_bytes=25_000_000,original_payload_note='合成 PDF 文件头＋随机字节，仅用于吞吐与哈希校验；正常及加密预览另用有效 PDF 验证。',list_sql=queries,home_full_request=home,list_full_request=listing,analysis_full_request=analysis,analysis_zip=exporting,analysis_zip_bytes=export_bytes[-1],complete_backup=backup_timing,backup_zip_ms=round(zip_ms,2),backup_zip_bytes=zipped.stat().st_size,independent_restore_ms=round(restore_ms,2),restored_counts=counts,measurement='查询／页面每组15次，导出／快照每组3次；本机 Flask test_client 含模板，不含 HTTP 网络与浏览器绘制。首次读取在数据生成后，OS可能已缓存；后续热缓存。列表测量前已完成偏好和日常快照准备。导出含全部元数据／正文／25MB原件压缩，完整备份含原件复制和校验；独立恢复含解压、校验、恢复前快照和切换。')
        c.c.close()
        return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent.parent/'test-output'/'benchmark-v1.0.2.json');args=parser.parse_args()
    result=run()
    out=args.output
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
