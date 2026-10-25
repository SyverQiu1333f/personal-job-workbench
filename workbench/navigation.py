"""Module hierarchy navigation, independent of referrers and browser history."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def list_context(module, back):
    """Keep the same module's list conditions from a bounded nested return path."""
    seen = set()
    for _ in range(12):
        if not isinstance(back, str) or back in seen or not back.startswith('/') or back.startswith('//') or '\\' in back:
            break
        seen.add(back)
        try:
            parts = urlsplit(back)
        except ValueError:
            break
        if parts.scheme or parts.netloc:
            break
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'saved']
        if parts.path == module:
            return urlunsplit(('', '', module, urlencode([(key, value) for key, value in query if key != 'back']), parts.fragment))
        back = dict(query).get('back', '')
    return module


def module_parent(endpoint, route_values=None, back='', job_id=None, restore_preview=False):
    values = route_values or {}
    if endpoint == 'index':
        return None, ''
    if endpoint == 'backup' and restore_preview:
        return '/backup', '返回备份列表'
    if endpoint in ['jobs', 'resumes', 'materials', 'analysis', 'backup']:
        return None, ''
    if endpoint in ['export', 'download_backup']:
        return '/backup', '返回导出与备份'
    if endpoint == 'tags':
        return list_context('/jobs', back), '返回岗位列表'
    if endpoint == 'platform_edit':
        ident = job_id or values.get('job_id')
        module, label = '/jobs', '岗位'
        edit = True
    else:
        module, label = {
            'edit_job': ('/jobs', '岗位'), 'job_detail': ('/jobs', '岗位'), 'job_flag': ('/jobs', '岗位'),
            'resume_edit': ('/resumes', '简历'), 'resume_detail': ('/resumes', '简历'),
            'material_edit': ('/materials', '资料'), 'material_detail': ('/materials', '资料'),
        }.get(endpoint, ('', ''))
        if not module:
            return '/', '返回总览'
        ident = values.get('ident')
        edit = endpoint in ['edit_job', 'resume_edit', 'material_edit', 'job_flag']
    destination = list_context(module, back)
    if edit and ident:
        detail = module + '/' + str(ident)
        if destination != module:
            detail += '?' + urlencode({'back': destination})
        return detail, '返回' + label + '详情'
    if ident and not urlsplit(destination).fragment:
        prefix = {'/jobs': 'job', '/resumes': 'resume', '/materials': 'material'}[module]
        destination += '#' + prefix + '-' + str(ident)
    return destination, '返回' + label + '列表'
