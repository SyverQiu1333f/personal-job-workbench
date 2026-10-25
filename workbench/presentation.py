"""Read-only page presentation and immutable stored history."""
import json
from datetime import datetime
from .storage import LOCAL

BOOL_FIELDS = {'star', 'archived', 'hidden', 'communicated', 'sent'}


def history_summaries(rows):
    """Read-only display buckets, not an inferred count of save transactions.

    Legacy history has second-resolution timestamps, not operation IDs. Keep
    adjacent fields for the same object and instant together; repeated fields
    start another bucket so repeated state transitions never disappear.
    """
    groups = []
    for row in rows:
        key = (row['created_at'], row['entity'], row['entity_id'])
        if not groups or groups[-1]['key'] != key or row['field'] in groups[-1]['fields']:
            groups.append(dict(key=key, created_at=row['created_at'], entity=row['entity'], entity_id=row['entity_id'], fields=set(), changes=[]))
        groups[-1]['fields'].add(row['field'])
        groups[-1]['changes'].append(row)
    result = []
    for group in groups:
        changes = [row for row in group['changes'] if row['field'] not in {'created_at', 'updated_at'}]
        if not changes:
            continue
        values = {row['field']: row for row in changes}

        def decoded(field, column):
            try:
                return json.loads(values[field][column])
            except (TypeError, ValueError):
                return values[field][column]

        entity = group['entity']
        if entity == '岗位':
            if 'company' in values and decoded('company', 'old_value') is None:
                title = '收录岗位'
            else:
                actions = []
                if 'stage' in values:
                    title_before = history_value(values['stage']['old_value'], 'stage')
                    title_after = history_value(values['stage']['new_value'], 'stage')
                    actions.append(f'阶段：{title_before} → {title_after}')
                for field, yes, no in [('archived', '归档岗位', '恢复岗位'), ('star', '添加星标', '取消星标')]:
                    if field in values:
                        value = decoded(field, 'new_value')
                        actions.append(yes if value in [1, True, '1'] else no if value in [0, False, '0'] else '调整岗位标记')
                title = '；'.join(actions) if actions else '更新岗位信息'
                if set(values) == {'tags'}:
                    title = '调整岗位标签'
        elif entity == '平台':
            if 'platform' in values and decoded('platform', 'old_value') is None:
                title = '添加平台记录'
            elif all(field.endswith('_checked') or field == 'checked_at' for field in values):
                title = '核对平台进展'
            else:
                title = '更新平台进展'
        elif entity == '材料':
            title = '保存面试资料' if 'title' in values and decoded('title', 'old_value') is None else '更新面试资料'
        elif entity == '简历':
            title = '关联最终简历' if 'name' in values and decoded('name', 'old_value') is None else '更新简历'
        elif entity == '标签':
            title = '重命名标签'
        elif entity in ['备注附件', '资料附件']:
            title = ('添加' if 'original_name' in values else '收起' if 'hidden' in values and decoded('hidden', 'new_value') in [1, True, '1'] else '恢复') + entity
        else:
            title = '更新' + entity
        result.append(dict(created_at=group['created_at'], entity=entity, entity_id=group['entity_id'], title=title, changes=changes))
    return result


def salary_range(item):
    if item.get('salary'):
        return item['salary']
    low, high = item.get('salary_min'), item.get('salary_max')
    if low is not None and high is not None:
        return f'{low}–{high} 元'
    if low is not None:
        return f'{low} 元起'
    if high is not None:
        return f'{high} 元以内'
    return ''


def breadcrumb_trail(endpoint, item=None, heading=''):
    """Use route identity and rendered record names, never strip title digits."""
    item = item or {}
    groups = {
        'index': ('总览', '/'),
        **dict.fromkeys(['jobs', 'edit_job', 'job_detail', 'job_flag', 'tags', 'platform_edit'], ('岗位管理', '/jobs')),
        **dict.fromkeys(['resumes', 'resume_edit', 'resume_detail'], ('最终简历', '/resumes')),
        **dict.fromkeys(['materials', 'material_edit', 'material_detail'], ('面试资料', '/materials')),
        'analysis': ('统计分析', '/analysis'),
        **dict.fromkeys(['backup', 'export', 'download_backup'], ('导出与备份', '/backup')),
    }
    module, url = groups.get(endpoint, ('总览', '/'))
    leaf = heading or {
        'edit_job': '编辑岗位' if item.get('id') else '快速收录',
        'job_detail': item.get('title', ''),
        'job_flag': '操作未完成',
        'tags': '标签管理',
        'platform_edit': '集中补记',
        'resume_edit': '编辑简历信息' if item.get('id') else '导入最终 PDF',
        'resume_detail': item.get('name', ''),
        'material_edit': '编辑资料' if item.get('id') else '保存资料',
        'material_detail': item.get('title', ''),
    }.get(endpoint, '')
    trail = [('个人求职', '/')]
    trail.append((module, url if leaf else None))
    if leaf:
        trail.append((leaf, None))
    return trail


def history_value(raw, field):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        value = raw  # Legacy or malformed entries remain readable, never rewritten.
    if value is None or value == '':
        return '未填写'
    if field in BOOL_FIELDS:
        if value in (True, 1, '1'):
            return '是'
        if value in (False, 0, '0'):
            return '否'
    if isinstance(value, str):
        if field.endswith(('_at', '_checked')):
            try:
                moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if moment.tzinfo is not None:
                    return moment.astimezone(LOCAL).strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                pass
        if field == 'tags':
            return '、'.join(value.replace('，', ',').split(','))
        return value
    if isinstance(value, list):
        if not value:
            return '无关联' if field == 'jobs' else '无'
        return '、'.join(('岗位 #' if field == 'jobs' else '') + str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
