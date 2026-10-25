import copy
import io
import json
import zipfile
from unittest.mock import patch

from workbench import db
from workbench.presentation import history_summaries
from test_jobs import env, job, post


def row(ident, field, old, new, entity='岗位', entity_id=1):
    return dict(id=ident, job_id=1, entity=entity, entity_id=entity_id, field=field,
                old_value=json.dumps(old, ensure_ascii=False), new_value=json.dumps(new, ensure_ascii=False), created_at='2026-10-20T01:00:00Z')


def test_creation_is_one_readable_summary_without_mutating_raw_history():
    rows = [row(4, 'updated_at', None, '2026-10-20T01:00:00Z'), row(3, 'note', None, '原始正文'), row(2, 'title', None, '产品经理'), row(1, 'company', None, '测试公司')]
    original = copy.deepcopy(rows)
    result = history_summaries(rows)
    assert len(result) == 1 and result[0]['title'] == '收录岗位'
    assert {r['field'] for r in result[0]['changes']} == {'note', 'title', 'company'}
    assert rows == original


def test_repeated_same_second_transitions_and_object_ids_are_preserved():
    rows = [row(5, 'star', 1, 0), row(4, 'star', 0, 1),
            row(3, 'stage', '待准备', '已结束'),
            row(2, 'reply', '待核对', '已有回复', '平台', 2),
            row(1, 'reply', '待核对', '已有回复', '平台', 1)]
    result = history_summaries(rows)
    assert len(result) == 4
    assert result[0]['title'] == '取消星标'
    assert '添加星标' in result[1]['title'] and '待准备 → 已结束' in result[1]['title']
    assert [r['entity_id'] for r in result[-2:]] == [2, 1]
    assert sum(len(r['changes']) for r in result) == len(rows)


def test_timestamp_noise_is_hidden_but_file_and_legacy_changes_remain():
    assert history_summaries([row(1, 'updated_at', 'old', 'new')]) == []
    rows = [row(3, 'file_id', 1, 2, '材料'), row(2, 'body', '旧文', '新文', '材料')]
    result = history_summaries(rows)
    assert result[0]['title'] == '更新面试资料' and len(result[0]['changes']) == 2
    legacy = row(1, 'note', '原值', '新值')
    legacy['old_value'] = '{legacy'
    assert history_summaries([legacy])[0]['changes'][0]['old_value'] == '{legacy'


def test_recent_summaries_and_older_details_keep_database_and_export_complete(env):
    app, client, csrf = env
    with patch('workbench.db.now', return_value='2026-10-20T01:00:00Z'):
        assert job(client, csrf).status_code == 302
    for n in range(7):
        with patch('workbench.db.now', return_value=f'2026-10-20T01:00:0{n+1}Z'):
            assert post(client, csrf, '/jobs/1/flag', row_version=str(n+1), field='star', value=str((n+1) % 2)).status_code == 302
    database = db.Database(app.extensions['store'].path)
    original = database.all('SELECT * FROM history ORDER BY id')
    page = client.get('/jobs/1').data.decode()
    current, older = page.split('<details class="history-older">')
    assert current.count('<details class="history-entry">') == 5
    assert older.count('<details class="history-entry">') == 3
    assert '查看更早记录' in older and '系统更新时间' not in page
    assert database.all('SELECT * FROM history ORDER BY id') == original
    with zipfile.ZipFile(io.BytesIO(client.get('/export?job_id=1').data)) as archive:
        exported = json.loads(archive.read('relations.json'))['history']
        assert exported == original
    database.c.close()
