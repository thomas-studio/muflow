"""Extract TXT/DOCX without modifying source; preserve paragraph anchors."""
import argparse, hashlib, json, zipfile, xml.etree.ElementTree as ET
from pathlib import Path
from core import save

def extract(path):
    p=Path(path); raw=p.read_bytes()
    if p.suffix.lower()=='.docx':
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        with zipfile.ZipFile(p) as z: root=ET.fromstring(z.read('word/document.xml'))
        lines=[''.join(n.text or '' for n in para.findall('.//w:t',ns)) for para in root.findall('.//w:body/w:p',ns)]
        # Table paragraphs remain in document order too.
        lines=[''.join(n.text or '' for n in para.findall('.//w:t',ns)) for para in root.findall('.//w:body//w:p',ns)]
    elif p.suffix.lower() in ('.txt','.md'):
        text=None
        encodings=('utf-16',) if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else ('utf-8-sig','gb18030')
        for enc in encodings:
            try:
                candidate=raw.decode(enc)
                if '\x00' not in candidate: text=candidate; break
            except UnicodeError: pass
        if text is None: raise ValueError('Cannot decode source; inspect encoding')
        lines=text.splitlines()
    else: raise ValueError('Use text/DOCX extraction; OCR/PDF requires available document tools')
    return {'source_name':p.name,'sha256':hashlib.sha256(raw).hexdigest(),
            'paragraphs':[{'id':f'SRC-{i:06d}','text':s} for i,s in enumerate(lines,1)],
            'extraction':'text-only; document layout and semantic completeness require agent review'}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('source');a.add_argument('output');x=a.parse_args()
    if Path(x.output).exists(): a.error('Output exists; use a new version')
    data=extract(x.source); save(x.output,data);print(json.dumps({'paragraphs':len(data['paragraphs']),'output':x.output},ensure_ascii=False))
