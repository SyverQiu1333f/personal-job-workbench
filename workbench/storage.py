import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import uuid
import zipfile
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from . import db

LOCAL = timezone(timedelta(hours=8))
LIMIT = 50_000_000
IMAGE_FORMATS = {'png': 'PNG', 'jpg': 'JPEG', 'webp': 'WEBP'}
IMAGE_MIMES = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp'}


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.current = self.root / 'current'
        self.backups = self.root / 'backups'
        self.staging = self.root / 'staging'
        self.lock = threading.RLock()
        self.maintenance = False
        self.root.mkdir(parents=True, exist_ok=True)
        # Recover a process interruption between the two generation renames.
        marker = self.root / 'restore-switch.json'
        if marker.exists():
            state = json.loads(marker.read_text('utf-8'))
            previous = self.root / state['previous']
            if previous.parent.resolve() != self.root or not previous.name.startswith('previous-'):
                raise ValueError('恢复切换日志无效，请保留数据并检查')
            if previous.exists():
                if self.current.exists():
                    os.replace(self.current, self.root / ('interrupted-' + uuid.uuid4().hex))
                os.replace(previous, self.current)
            marker.unlink()
        for p in [self.current / 'files', self.backups, self.staging]:
            p.mkdir(parents=True, exist_ok=True)
        config = self.root / 'config.local.toml'
        if not config.exists():
            identity = uuid.uuid4().hex
            if self.path.exists():
                existing = db.Database(self.path)
                try:
                    meta = existing.one("SELECT value FROM db_meta WHERE key='workspace_id'")
                    if meta:
                        identity = meta['value']
                finally:
                    existing.c.close()
            with config.open('x', encoding='utf-8') as f:
                f.write(f'workspace_id = "{identity}"\nhost = "127.0.0.1"\nport = 8765\n')
        import tomllib
        self.config = tomllib.loads(config.read_text('utf-8'))
        if self.config['host'] not in ('127.0.0.1', 'localhost'):
            raise ValueError('首版仅允许回环地址')
        self.workspace_id = self.config['workspace_id']
        epoch = self.root / 'data-epoch.txt'
        if not epoch.exists():
            epoch.write_text(uuid.uuid4().hex, 'ascii')
        self.epoch = epoch.read_text('ascii')
        db.init(self.path, self.workspace_id, lambda: self.backup('pre-migrate'))

    @property
    def path(self):
        return self.current / 'db.sqlite3'

    @contextmanager
    def coordinated(self):
        if not self.lock.acquire(timeout=30):
            raise ValueError('有操作正在进行，请稍后重试')
        try:
            yield
        finally:
            self.lock.release()

    def put(self, upload, resume=False, note=False):
        ext = Path(upload.filename).suffix.lower().lstrip('.')
        if note and ext == 'jpeg':
            ext = 'jpg'
        allowed = ['pdf'] if resume else ['pdf', *IMAGE_FORMATS] if note else ['pdf', 'md', 'markdown', 'docx', 'doc']
        if ext not in allowed:
            raise ValueError('备注附件仅支持 PDF、PNG、JPG/JPEG、WebP' if note else '简历仅支持 PDF；材料支持 Markdown、PDF、Word')
        with self.coordinated():
            fd, temp = tempfile.mkstemp(dir=self.staging)
            temp = Path(temp)
            try:
                size = 0
                with os.fdopen(fd, 'wb') as out:
                    while chunk := upload.stream.read(1024 * 1024):
                        size += len(chunk)
                        if size > LIMIT:
                            raise ValueError('单个附件超过 50MB（50,000,000 字节）；其他输入已保留，请重新选择文件')
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
                head = temp.read_bytes()[:16] if size < 16 else self._head(temp)
                body = None
                if ext == 'pdf' and not head.startswith(b'%PDF-'):
                    raise ValueError('文件内容不是可识别的 PDF')
                if ext in IMAGE_FORMATS:
                    from PIL import Image
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter('error', Image.DecompressionBombWarning)
                            with Image.open(temp, formats=list(IMAGE_FORMATS.values())) as picture:
                                if picture.format != IMAGE_FORMATS[ext]:
                                    raise ValueError('图片格式不匹配')
                                picture.verify()
                            with Image.open(temp, formats=[IMAGE_FORMATS[ext]]) as picture:
                                picture.load()
                    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
                        raise ValueError('图片尺寸过大，无法预览，请缩小后重新选择')
                    except (OSError, ValueError, SyntaxError):
                        raise ValueError('图片无法识别、格式不匹配或已损坏，请重新选择')
                if ext == 'doc' and not head.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
                    raise ValueError('Word 文件头无效')
                if ext == 'docx':
                    try:
                        with zipfile.ZipFile(temp) as z:
                            if 'word/document.xml' not in z.namelist():
                                raise ValueError('DOCX 缺少 Word 文档')
                    except zipfile.BadZipFile:
                        raise ValueError('DOCX 文件格式无效')
                if ext in ('md', 'markdown'):
                    raw = temp.read_bytes()
                    for encoding in ['utf-8-sig', 'gb18030']:
                        try:
                            body = raw.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            pass
                    if body is None or '\x00' in body or any(ord(ch)<32 and ch not in '\t\n\r' for ch in body):
                        raise ValueError('Markdown 必须为 UTF-8 或 GB18030 文本')
                h = digest(temp)
                rel = f'files/{h[:2]}/{h}.{ext}'
                dest = self.current / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    os.replace(temp, dest)
                elif digest(dest) != h:
                    raise ValueError('共享原件校验失败，未覆盖文件')
                return dict(hash=h, ext=ext, path=rel, size=size, original_name=upload.filename, created_at=db.now()), body
            finally:
                temp.unlink(missing_ok=True)

    @staticmethod
    def _head(path):
        with path.open('rb') as f:
            return f.read(16)

    def backup(self, purpose='manual'):
        with self.coordinated():
            while True:
                ident = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
                dest = self.backups / ident
                try:
                    dest.mkdir()
                    break
                except FileExistsError:
                    continue
            try:
                src = db.connect(self.path)
                target = sqlite3.connect(dest / 'db.sqlite3')
                try:
                    src.backup(target)
                finally:
                    src.close()
                    target.close()
                snap = db.Database(dest / 'db.sqlite3')
                try:
                    files = db.referenced_files(snap)
                    counts = {t: snap.one(f'SELECT count(*) n FROM {t}')['n'] for t in ['jobs', 'platforms', 'tags', 'resumes', 'materials', 'job_tags', 'job_resumes', 'job_materials']}
                    counts['job_attachments'] = snap.one('SELECT count(*) n FROM job_attachments')['n'] if db.has_table(snap, 'job_attachments') else 0
                    counts['material_attachments'] = snap.one('SELECT count(*) n FROM material_attachments')['n'] if db.has_table(snap, 'material_attachments') else 0
                    schema_version = int(snap.one("SELECT value FROM db_meta WHERE key='schema_version'")['value'])
                finally:
                    snap.c.close()
                for f in files:
                    origin = self.current / f['path']
                    end = dest / f['path']
                    end.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(origin, end)
                    if end.stat().st_size != f['size'] or digest(end) != f['hash']:
                        raise ValueError(f"原件损坏：{f['original_name']}")
                meta = dict(backup_id=ident, purpose=purpose, status='complete', schema_version=schema_version, app_version=db.VERSION, sqlite_version=sqlite3.sqlite_version, created_at=db.now(), local_day=datetime.now(LOCAL).date().isoformat(), counts=counts, files=files, db_hash=digest(dest / 'db.sqlite3'))
                (dest / 'manifest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), 'utf-8')
                self.validate(dest)
                return dest
            except Exception:
                # Incomplete backups are visible, never count toward successful rotation.
                (dest / 'error.txt').write_text('备份失败；未发布，不可恢复。\n' + db.now(), 'utf-8')
                manifest = dest / 'manifest.json'
                if manifest.exists():
                    failed = json.loads(manifest.read_text('utf-8'))
                    failed['status'] = 'failed'
                    manifest.write_text(json.dumps(failed, ensure_ascii=False), 'utf-8')
                raise

    def list_backups(self):
        result = []
        for p in self.backups.iterdir():
            if p.is_dir():
                try:
                    m = json.loads((p / 'manifest.json').read_text('utf-8'))
                    m.update(size=sum(x.stat().st_size for x in p.rglob('*') if x.is_file()), location=str(p))
                    result.append(m)
                except (OSError, ValueError):
                    result.append(dict(backup_id=p.name, status='failed', purpose='unknown', created_at='', size=0, location=str(p)))
        return sorted(result, key=lambda m: m['backup_id'], reverse=True)

    def daily(self):
        with self.coordinated():
            day = datetime.now(LOCAL).date().isoformat()
            items = [b for b in self.list_backups() if b['status'] == 'complete' and b['purpose'] == 'daily']
            if any(b['local_day'] == day for b in items):
                return
            self.backup('daily')
            # Only our own successful daily directories are eligible for rotation.
            items = [b for b in self.list_backups() if b['status'] == 'complete' and b['purpose'] == 'daily']
            keep_days = sorted({b['local_day'] for b in items}, reverse=True)[:7]
            for item in items:
                if item['local_day'] not in keep_days:
                    path = (self.backups / item['backup_id']).resolve()
                    if path.parent == self.backups.resolve():
                        try:
                            shutil.rmtree(path)
                        except OSError:
                            pass

    @staticmethod
    def validate(folder):
        folder = Path(folder)
        m = json.loads((folder / 'manifest.json').read_text('utf-8'))
        if m.get('status') != 'complete' or m.get('schema_version') not in (1, 2, 3, 4, db.SCHEMA):
            raise ValueError('备份未完成或结构版本不兼容（支持 schema 1／2／3／4→5、5）')
        if digest(folder / 'db.sqlite3') != m['db_hash']:
            raise ValueError('数据库校验失败')
        c = db.Database(folder / 'db.sqlite3')
        try:
            if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.all('PRAGMA foreign_key_check'):
                raise ValueError('数据库或关联完整性错误')
            if c.one("SELECT value FROM db_meta WHERE key='schema_version'")['value'] != str(m['schema_version']):
                raise ValueError('数据库结构版本与清单不符')
            if m['schema_version'] >= 3 and not db.has_table(c, 'job_attachments'):
                raise ValueError('备份缺少岗位备注附件表')
            if m['schema_version'] >= 4 and not db.has_table(c, 'material_attachments'):
                raise ValueError('备份缺少资料补充附件表')
            if m['schema_version'] >= 5 and 'company_size' not in [r['name'] for r in c.all('PRAGMA table_info(jobs)')]:
                raise ValueError('备份缺少公司规模字段')
            referenced = db.referenced_files(c)
            for f in c.all('SELECT * FROM files'):
                if not re.fullmatch('[a-f0-9]{64}', f['hash']) or f['ext'] not in ['pdf','md','markdown','doc','docx',*IMAGE_MIMES] or f['path'] != f"files/{f['hash'][:2]}/{f['hash']}.{f['ext']}":
                    raise ValueError('数据库包含不安全原件路径')
            listed = {f['path']: f for f in m['files']}
            if len(listed) != len(m['files']) or set(listed) != {f['path'] for f in referenced}:
                raise ValueError('原件清单与数据库引用不一致')
            for f in referenced:
                p = folder / f['path']
                if not p.resolve().is_relative_to(folder.resolve()) or not f['path'].startswith('files/'):
                    raise ValueError('备份路径不安全')
                if listed[f['path']]['hash'] != f['hash'] or not p.is_file() or p.stat().st_size != f['size'] or digest(p) != f['hash']:
                    raise ValueError(f"原件缺失或损坏：{f['original_name']}")
        finally:
            c.c.close()
        return m

    def unpack(self, stream):
        folder = self.staging / uuid.uuid4().hex
        folder.mkdir()
        try:
            with zipfile.ZipFile(stream) as z:
                seen = set()
                for info in z.infolist():
                    name = PurePosixPath(info.filename)
                    if name.is_absolute() or '..' in name.parts or '\\' in info.filename or ':' in info.filename or info.filename in seen:
                        raise ValueError('备份包含不安全或重复路径')
                    seen.add(info.filename)
                    dest = folder / info.filename
                    if not dest.resolve().is_relative_to(folder.resolve()):
                        raise ValueError('备份路径越界')
                    if info.is_dir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with z.open(info) as src, dest.open('xb') as out:
                            shutil.copyfileobj(src, out)
            self.validate(folder)
            return folder
        except Exception:
            shutil.rmtree(folder)
            raise

    def restore(self, folder):
        with self.coordinated():
            meta = self.validate(folder)
            self.backup('pre-restore')
            self.maintenance = True
            old = self.root / ('previous-' + uuid.uuid4().hex)
            moved = False
            marker = self.root / 'restore-switch.json'
            with marker.open('w', encoding='utf-8') as f:
                json.dump({'previous': old.name}, f)
                f.flush()
                os.fsync(f.fileno())
            try:
                # Callers close every request connection before this generation switch.
                os.replace(self.current, old)
                moved = True
                os.replace(folder, self.current)
                c = db.Database(self.path)
                try:
                    c.execute("UPDATE db_meta SET value=? WHERE key='workspace_id'", (self.workspace_id,))
                    c.c.commit()
                finally:
                    c.c.close()
                db.init(self.path, self.workspace_id, lambda: self.backup('pre-migrate'))
                new_epoch = uuid.uuid4().hex
                temporary_epoch = self.root / 'data-epoch.tmp'
                temporary_epoch.write_text(new_epoch, 'ascii')
                os.replace(temporary_epoch, self.root / 'data-epoch.txt')
                self.epoch = new_epoch
                return meta
            except Exception:
                if moved:
                    if self.current.exists():
                        failed = self.staging / ('failed-' + uuid.uuid4().hex)
                        os.replace(self.current, failed)
                    os.replace(old, self.current)
                raise
            finally:
                self.maintenance = False
                marker.unlink(missing_ok=True)

    @staticmethod
    def zip_backup(folder, output):
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in Path(folder).rglob('*'):
                if p.is_file():
                    z.write(p, p.relative_to(folder).as_posix())
