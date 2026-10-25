"""Offline PDF rendering; JSON transport avoids native PDF download interception."""
import base64
from flask import abort, g, jsonify, render_template, request


def register(app):
    store=app.extensions['store']

    def info(ident):
        f=g.db.one("SELECT * FROM files WHERE id=? AND ext='pdf'",(ident,))
        if not f:abort(404)
        path=(store.current/f['path']).resolve()
        if not path.is_relative_to((store.current/'files').resolve()) or not path.is_file():
            abort(404,'PDF 原件缺失，请从完整备份恢复')
        return f,path

    @app.get('/preview/<int:ident>')
    def preview(ident):
        f,path=info(ident)
        return render_template('pdf_preview.html',file=f)

    @app.post('/preview-data/<int:ident>')
    def preview_data(ident):
        try:
            app.extensions['helpers']['check_epoch']()
            f,path=info(ident)
            start=int(request.form.get('offset','0'))
            if start<0 or start>=f['size']:
                raise ValueError('PDF 分段位置无效')
            with path.open('rb') as stream:
                stream.seek(start)
                raw=stream.read(1024*1024)
            if not raw:raise ValueError('PDF 原件内容不完整')
            return jsonify(data=base64.b64encode(raw).decode('ascii'),size=len(raw))
        except (ValueError,OSError) as exc:
            return jsonify(error=str(exc)),409
