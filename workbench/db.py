"""All SQLite connections, queries and transaction boundaries live here."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from .validation import page_number

SCHEMA = 5
VERSION = '1.1.2'
STAGES = ['待准备', '准备中', '沟通／投递中', '面试中', '已获 Offer', '已结束']
KINDS = ['背调', '模拟面试', '真实面试复盘', '外部分析结果']
FIELD_LABELS = dict(company='公司',title='岗位／资料标题',source='来源',url='来源链接',jd='JD 原文',salary='薪资原文',salary_min='薪资下限',salary_max='薪资上限',city='城市',region='区域',address='办公地点',direct='公共交通直达',commute='通勤分钟',commute_note='通勤说明',stage='整体阶段',end_reason='结束原因',note='备注',star='星标',archived='归档',tags='标签',jobs='关联岗位',platform='平台',communicated='发起沟通',communication_date='沟通日期',sent='发送简历',sent_date='发送日期',resume_id='实际发送简历 ID',reading='阅读',reply='回复',reply_kind='回复性质',reply_date='首次回复日期',invitation='邀约',invitation_date='首次邀约日期',reading_checked='阅读核对时间',reply_checked='回复核对时间',invitation_checked='邀约核对时间',checked_at='整条核对时间',updated_at='系统更新时间',name='展示名称',kind='资料类型',body='正文',file_id='原件 ID',hidden='日常收起',original_name='原始文件名')


FIELD_LABELS['company_size'] = '公司规模'


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def literal_like(value):
    """LIKE pattern for ordinary text; callers use ESCAPE '!'."""
    return '%' + value.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'


DDL = '''
CREATE TABLE db_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT NOT NULL,row_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE jobs(id INTEGER PRIMARY KEY,company TEXT NOT NULL,title TEXT NOT NULL,source TEXT DEFAULT '',url TEXT DEFAULT '',jd TEXT DEFAULT '',salary TEXT DEFAULT '',salary_min INTEGER,salary_max INTEGER,city TEXT DEFAULT '宁波',region TEXT DEFAULT '',address TEXT DEFAULT '',direct TEXT DEFAULT '未知',commute INTEGER,commute_note TEXT DEFAULT '',stage TEXT DEFAULT '待准备',end_reason TEXT DEFAULT '',note TEXT DEFAULT '',star INTEGER DEFAULT 0,archived INTEGER DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,row_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE tags(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL,row_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE job_tags(job_id INTEGER REFERENCES jobs(id),tag_id INTEGER REFERENCES tags(id),PRIMARY KEY(job_id,tag_id));
CREATE TABLE files(id INTEGER PRIMARY KEY,hash TEXT NOT NULL,ext TEXT NOT NULL,path TEXT UNIQUE NOT NULL,size INTEGER NOT NULL,original_name TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE resumes(id INTEGER PRIMARY KEY,name TEXT NOT NULL,file_id INTEGER NOT NULL REFERENCES files(id),note TEXT DEFAULT '',hidden INTEGER DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,row_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE job_resumes(job_id INTEGER REFERENCES jobs(id),resume_id INTEGER REFERENCES resumes(id),PRIMARY KEY(job_id,resume_id));
CREATE TABLE platforms(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL REFERENCES jobs(id),platform TEXT DEFAULT '',url TEXT DEFAULT '',communicated INTEGER DEFAULT 0,communication_date TEXT,sent INTEGER DEFAULT 0,sent_date TEXT,resume_id INTEGER REFERENCES resumes(id),reading TEXT DEFAULT '未知',reply TEXT DEFAULT '待核对',reply_kind TEXT DEFAULT '',reply_date TEXT,invitation TEXT DEFAULT '待核对',invitation_date TEXT,reading_checked TEXT,reply_checked TEXT,invitation_checked TEXT,checked_at TEXT,note TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,row_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE materials(id INTEGER PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,body TEXT DEFAULT '',file_id INTEGER REFERENCES files(id),source TEXT DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,row_version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE job_materials(job_id INTEGER REFERENCES jobs(id),material_id INTEGER REFERENCES materials(id),PRIMARY KEY(job_id,material_id));
CREATE TABLE history(id INTEGER PRIMARY KEY,job_id INTEGER REFERENCES jobs(id),entity TEXT NOT NULL,entity_id INTEGER,field TEXT NOT NULL,old_value TEXT,new_value TEXT,created_at TEXT NOT NULL);
CREATE TABLE request_log(request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,result_kind TEXT NOT NULL,result_ref TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX jobs_list ON jobs(archived,star DESC,created_at DESC,id DESC);
CREATE INDEX jobs_stage ON jobs(stage);
CREATE INDEX platform_job ON platforms(job_id);
CREATE INDEX platform_dates ON platforms(communication_date,sent_date);
CREATE INDEX material_job ON job_materials(material_id,job_id);
CREATE INDEX resume_file ON resumes(file_id);
CREATE INDEX history_job ON history(job_id,id);
'''

NOTE_DDL = [
    '''CREATE TABLE job_attachments(id INTEGER PRIMARY KEY,job_id INTEGER NOT NULL REFERENCES jobs(id),file_id INTEGER NOT NULL REFERENCES files(id),original_name TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)),created_at TEXT NOT NULL,UNIQUE(job_id,file_id))''',
    'CREATE INDEX attachment_job ON job_attachments(job_id,hidden,id)',
    'CREATE INDEX attachment_file ON job_attachments(file_id)',
]

MATERIAL_NOTE_DDL = [
    '''CREATE TABLE material_attachments(id INTEGER PRIMARY KEY,material_id INTEGER NOT NULL REFERENCES materials(id),file_id INTEGER NOT NULL REFERENCES files(id),original_name TEXT NOT NULL,hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0,1)),created_at TEXT NOT NULL,UNIQUE(material_id,file_id))''',
    'CREATE INDEX material_attachment_owner ON material_attachments(material_id,hidden,id)',
    'CREATE INDEX material_attachment_file ON material_attachments(file_id)',
]


COMPANY_SIZE_DDL = ["ALTER TABLE jobs ADD COLUMN company_size TEXT NOT NULL DEFAULT ''"]


def has_table(database, name):
    return database.one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None


def referenced_files(database):
    query = 'SELECT DISTINCT f.* FROM files f WHERE EXISTS(SELECT 1 FROM resumes r WHERE r.file_id=f.id) OR EXISTS(SELECT 1 FROM materials m WHERE m.file_id=f.id)'
    if has_table(database, 'job_attachments'):
        query += ' OR EXISTS(SELECT 1 FROM job_attachments a WHERE a.file_id=f.id)'
    if has_table(database, 'material_attachments'):
        query += ' OR EXISTS(SELECT 1 FROM material_attachments a WHERE a.file_id=f.id)'
    return database.all(query)


def connect(path):
    c = sqlite3.connect(path, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    if c.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        c.close()
        raise RuntimeError('外键保护未启用')
    c.execute('PRAGMA busy_timeout=5000')
    return c


def init(path, workspace_id, before_migrate=None):
    fresh = not path.exists()
    c = connect(path)
    try:
        if fresh:
            c.executescript(DDL)
            c.executemany('INSERT INTO db_meta VALUES(?,?)', [('schema_version', '1'), ('workspace_id', workspace_id)])
            c.commit()
        version = c.execute("SELECT value FROM db_meta WHERE key='schema_version'").fetchone()
        if not version or int(version[0]) not in (1, 2, 3, 4, SCHEMA):
            raise RuntimeError('数据库结构不兼容：支持 schema 1／2／3／4→5 升级与 5；请使用对应代码或经验证的完整备份恢复，禁止写入。')
        identity = c.execute("SELECT value FROM db_meta WHERE key='workspace_id'").fetchone()
        if not identity or identity[0] != workspace_id:
            raise RuntimeError('工作区配置与数据库身份不一致，请保留资料并通过完整备份恢复，不可混用数据目录。')
        if int(version[0]) < SCHEMA:
            if not fresh:
                if before_migrate is None:
                    raise RuntimeError('旧结构需要完整迁移前备份，禁止直接升级')
                before_migrate()
            try:
                c.execute('BEGIN IMMEDIATE')
                if int(version[0]) == 1:
                    c.execute("ALTER TABLE resumes ADD COLUMN original_name TEXT NOT NULL DEFAULT ''")
                    c.execute("ALTER TABLE materials ADD COLUMN original_name TEXT NOT NULL DEFAULT ''")
                    c.execute('UPDATE resumes SET original_name=(SELECT original_name FROM files WHERE id=resumes.file_id)')
                    c.execute("UPDATE materials SET original_name=coalesce((SELECT original_name FROM files WHERE id=materials.file_id),'')")
                for statement in (NOTE_DDL if int(version[0]) < 3 else []) + (MATERIAL_NOTE_DDL if int(version[0]) < 4 else []) + COMPANY_SIZE_DDL:
                    c.execute(statement)
                c.execute("UPDATE db_meta SET value='5' WHERE key='schema_version'")
                if c.execute('PRAGMA foreign_key_check').fetchall():
                    raise RuntimeError('迁移关联校验失败')
                c.commit()
            except Exception:
                c.rollback()
                raise RuntimeError('迁移失败，结构与业务事务已回滚；迁移前完整备份已保留。停止启动。')
        if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_attachments'").fetchone():
            raise RuntimeError('数据库结构不完整：缺少岗位备注附件表，请保留资料并检查完整备份。')
        if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='material_attachments'").fetchone():
            raise RuntimeError('数据库结构不完整：缺少资料补充附件表，请保留资料并检查完整备份。')
        if 'company_size' not in [row[1] for row in c.execute('PRAGMA table_info(jobs)')]:
            raise RuntimeError('数据库结构不完整：缺少公司规模，请保留资料并检查完整备份。')
        mode = c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        if mode.lower() != 'delete':
            raise RuntimeError('不能启用 DELETE 模式')
        for key, value in [('python_version', sys.version), ('sqlite_version', sqlite3.sqlite_version), ('journal_mode', mode), ('app_version', VERSION)]:
            c.execute('INSERT OR REPLACE INTO db_meta VALUES(?,?)', (key, value))
        c.commit()
    finally:
        c.close()


class Conflict(ValueError):
    pass


class Database:
    def __init__(self, path):
        self.c = connect(path)

    def all(self, sql, args=()):
        return [dict(r) for r in self.c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        row = self.c.execute(sql, args).fetchone()
        return dict(row) if row else None

    def execute(self, sql, args=()):
        return self.c.execute(sql, args)

    def paginate(self, sql, args, page):
        total=self.one('SELECT count(*) n FROM ('+sql+')',args)['n']
        rows=self.all(sql+' LIMIT 50 OFFSET ?',(*args,(page_number(page)-1)*50))
        return rows,total

    def insert(self, table, values):
        cols = list(values)
        return self.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", tuple(values.values())).lastrowid

    def update(self, table, ident, expected, values):
        count = self.execute(f"UPDATE {table} SET {','.join(k+'=?' for k in values)},row_version=row_version+1 WHERE id=? AND row_version=?", (*values.values(), ident, expected)).rowcount
        if count != 1:
            raise Conflict('这条记录已被修改，请刷新后重试。当前输入已保留，可另开页面核对。')

    def changes(self, job_id, entity, ident, old, values):
        for key, value in values.items():
            before = old.get(key) if old else None
            if before != value:
                self.insert('history', dict(job_id=job_id, entity=entity, entity_id=ident, field=key, old_value=json.dumps(before, ensure_ascii=False), new_value=json.dumps(value, ensure_ascii=False), created_at=now()))

    def save(self, store, request_id, payload_hash, action):
        if not request_id:
            raise ValueError('缺少提交标识，请刷新表单')
        previous = self.one('SELECT * FROM request_log WHERE request_id=?', (request_id,))
        if previous:
            if previous['payload_hash'] != payload_hash:
                raise Conflict('该提交标识已用于另一次提交，请刷新后重新提交。')
            return previous['result_ref']
        store.daily()
        try:
            self.execute('BEGIN IMMEDIATE')
            # Cross-thread/process safeguards remain within the write transaction.
            previous = self.one('SELECT * FROM request_log WHERE request_id=?', (request_id,))
            if previous:
                if previous['payload_hash'] != payload_hash:
                    raise Conflict('该提交标识已用于另一次提交')
                self.c.rollback()
                return previous['result_ref']
            ref = str(action())
            self.insert('request_log', dict(request_id=request_id, payload_hash=payload_hash, result_kind='redirect', result_ref=ref, created_at=now()))
            self.c.commit()
            return ref
        except Exception:
            self.c.rollback()
            raise

    def job_filter(self, f):
        clauses, args = ['1=1'], []
        view = f.get('view', '')
        archived = f.get('archived', '1' if view == 'archived' else '0')
        if archived != 'all':
            clauses.append('j.archived=?'); args.append(int(archived == '1'))
        if f.get('q'):
            clauses.append("(j.company LIKE ? ESCAPE '!' OR j.title LIKE ? ESCAPE '!')"); args += [literal_like(f['q'])] * 2
        stage = '面试中' if view == 'interview' else f.get('stage')
        if stage:
            clauses.append('j.stage=?'); args.append(stage)
        if view == 'star' or f.get('star') == '1':
            clauses.append('j.star=1')
        if f.get('region'):
            clauses.append('j.region=?'); args.append(f['region'])
        tags = list(dict.fromkeys(t.strip() for t in f.get('tags', '').replace('，', ',').split(',') if t.strip()))
        if tags:
            query = 'SELECT count(DISTINCT t.id) FROM job_tags jt JOIN tags t ON t.id=jt.tag_id WHERE jt.job_id=j.id AND t.name IN (' + ','.join('?' for _ in tags) + ')'
            clauses.append('(' + query + ') >= ?'); args += tags + [len(tags) if f.get('tag_mode') == 'all' else 1]
        p, pa = ['p.job_id=j.id'], []
        if f.get('fact') == 'touched':
            p.append('(p.communicated=1 OR p.sent=1)')
        if f.get('fact') == 'sent':
            p.append('p.sent=1')
        if f.get('platform'):
            p.append('p.platform=?'); pa.append(f['platform'])
        reply = '已有回复' if view == 'reply' else f.get('reply')
        invitation = '已有明确邀约' if view == 'invitation' else f.get('invitation')
        for name, value in [('reply', reply), ('invitation', invitation)]:
            if value:
                p.append('p.' + name + '=?'); pa.append(value)
        if view == 'recent':
            from datetime import datetime, timedelta, timezone
            cutoff = (datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=6)).isoformat()
            p.append('p.sent=1 AND p.sent_date>=?'); pa.append(cutoff)
            p.append('p.sent_date<=?'); pa.append(datetime.now(timezone(timedelta(hours=8))).date().isoformat())
        if len(p) > 1:
            clauses.append('EXISTS(SELECT 1 FROM platforms p WHERE ' + ' AND '.join(p) + ')'); args += pa
        return ' AND '.join(clauses), args

    def jobs(self, filters, paged=True):
        where, args = self.job_filter(filters)
        total = self.one('SELECT count(*) n FROM jobs j WHERE ' + where, args)['n']
        order = 'j.star DESC,j.created_at DESC,j.id DESC' if filters.get('sort') != 'oldest' else 'j.star DESC,j.created_at ASC,j.id ASC'
        page = page_number(filters.get('page')) if paged else 1
        sql = 'SELECT j.*, (SELECT group_concat(t.name, "、") FROM job_tags jt JOIN tags t ON t.id=jt.tag_id WHERE jt.job_id=j.id) tags FROM jobs j WHERE ' + where + ' ORDER BY ' + order
        if paged:
            sql += ' LIMIT 50 OFFSET ?'; args = [*args, (page - 1) * 50]
        return self.all(sql, args), total

    def set_tags(self, job_id, names):
        self.execute('DELETE FROM job_tags WHERE job_id=?', (job_id,))
        for name in dict.fromkeys(n.strip() for n in names.replace('，', ',').split(',') if n.strip()):
            self.execute('INSERT OR IGNORE INTO tags(name) VALUES(?)', (name,))
            tag = self.one('SELECT id FROM tags WHERE name=?', (name,))
            self.execute('INSERT INTO job_tags VALUES(?,?)', (job_id, tag['id']))
