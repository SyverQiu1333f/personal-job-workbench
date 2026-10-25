import base64
import io
from pathlib import Path
from test_jobs import env,job,post


def test_offline_json_pdf_preview(env):
    app,c,csrf=env
    job(c,csrf)
    pdf=(Path(__file__).parent/'fixtures'/'样例简历.pdf').read_bytes()
    assert post(c,csrf,'/resumes/new',name='可打开 PDF',jobs=['1'],file=(io.BytesIO(pdf),'样例.pdf')).status_code==302
    response=c.get('/preview/1')
    assert response.status_code==200 and 'pdf-viewer.mjs'.encode() in response.data
    response=post(c,csrf,'/preview-data/1',offset='0')
    assert response.mimetype=='application/json'
    assert base64.b64decode(response.json['data'])==pdf
    assert 'Content-Disposition' not in response.headers
    assert c.get('/file/1',headers={'Range':'bytes=0-7'}).status_code==206
    for path in ['/static/pdf-viewer.mjs','/static/pdfjs/build/pdf.min.mjs','/static/pdfjs/build/pdf.worker.min.mjs','/static/pdfjs/standard_fonts/LiberationSans-Regular.ttf']:
        assert c.get(path).status_code==200
