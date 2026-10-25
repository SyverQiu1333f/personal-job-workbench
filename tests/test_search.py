import io
from urllib.parse import quote
from werkzeug.datastructures import FileStorage
from workbench import db
from test_jobs import env


def test_literal_search_across_all_three_libraries(env):
    app,c,csrf=env
    store=app.extensions['store']; d=db.Database(store.path)
    info,_=store.put(FileStorage(stream=io.BytesIO(b'%PDF-1.4\n search fixture'),filename='搜索.pdf'),True)
    fid=d.insert('files',info)
    names=['百分%','百分X','下划_','下划X','路径\\盘','感叹!','普通中文']
    for index,name in enumerate(names):
        jid=d.insert('jobs',dict(company=name,title='岗位'+str(index),created_at=db.now(),updated_at=db.now()))
        rid=d.insert('resumes',dict(name='简历'+name,file_id=fid,created_at=db.now(),updated_at=db.now(),original_name='搜索.pdf'))
        mid=d.insert('materials',dict(title='材料'+name,kind='背调',body='原文',created_at=db.now(),updated_at=db.now()))
        d.insert('job_resumes',dict(job_id=jid,resume_id=rid));d.insert('job_materials',dict(job_id=jid,material_id=mid))
        d.set_tags(jid,'精确标签_')
    d.c.commit()
    for term,name in [('%','百分%'),('_','下划_'),('\\','路径\\盘'),('!','感叹!'),('普通','普通中文')]:
        rows,total=d.jobs({'q':term})
        assert total==1 and rows[0]['company']==name
        for path in ['/jobs','/resumes','/materials']:
            response=c.get(path+'?q='+quote(term))
            assert response.status_code==200 and name.encode() in response.data
            if path!='/jobs':assert '共 1 条'.encode() in response.data
    assert d.jobs({'tags':'精确标签_'})[1]==7
    # Associated company lookup follows the same literal matching, even when names differ.
    d.execute("UPDATE resumes SET name='普通简历'");d.execute("UPDATE materials SET title='普通材料'");d.c.commit()
    assert '共 1 条'.encode() in c.get('/resumes?q=%25').data
    assert '共 1 条'.encode() in c.get('/materials?q=_').data
    d.c.close()
