"""Container/file integrity only: no decoding, transcript or content judgement."""
import json,os,re,struct,urllib.request,urllib.error,uuid
from pathlib import Path
from core import save,load,sha

def check_mp4(file):
    p=Path(file);size=p.stat().st_size;pos=0;atoms=[]
    with p.open('rb') as f:
        while pos<size:
            f.seek(pos);head=f.read(8)
            if len(head)!=8:raise ValueError('Truncated MP4 atom header')
            count,kind=struct.unpack('>I4s',head);header=8
            if count==1:
                ext=f.read(8)
                if len(ext)!=8:raise ValueError('Truncated MP4 extended size')
                count=struct.unpack('>Q',ext)[0];header=16
            elif count==0:count=size-pos
            if count<header or pos+count>size:raise ValueError('Truncated MP4 atom '+repr(kind)+': declared end exceeds actual file')
            atoms.append({'type':kind.decode('ascii','replace'),'offset':pos,'size':count});pos+=count
    kinds={a['type'] for a in atoms}
    if not {'ftyp','moov','mdat'}<=kinds:raise ValueError('MP4 container missing required atoms')
    if not any(a['type']=='mdat' and a['size']>8 for a in atoms):raise ValueError('Empty MP4 media payload')
    return {'bytes':size,'atoms':atoms,'status':'通过','scope':'top-level atom bounds/file completeness only; media content remains for user review'}

def check_file(p):
    if p.stat().st_size<=0:raise ValueError('Empty download')
    if p.suffix.lower()=='.mp4' or '.mp4.part' in p.name:return check_mp4(p)
    return {'bytes':p.stat().st_size,'status':'通过','scope':'transport size only'}

def download(url,dest,timeout=120,expected_sha256=None):
    dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        try:
            check_file(dest)
            if expected_sha256 and sha(dest)!=expected_sha256:raise ValueError('Saved output hash changed')
            return
        except ValueError:os.replace(dest,dest.with_name(dest.name+'.corrupt-'+uuid.uuid4().hex[:8]))
    part=dest.with_name(dest.name+'.part');meta=part.with_name(part.name+'.meta.json')
    start=part.stat().st_size if part.exists() else 0;old=load(meta) if meta.exists() else {}
    headers={'Range':f'bytes={start}-','Accept-Encoding':'identity','User-Agent':'muflow/2'}
    if old.get('etag'):headers['If-Range']=old['etag']
    req=urllib.request.Request(url,headers=headers)
    def finish():
        try:
            check_file(part)
            if expected_sha256 and sha(part)!=expected_sha256:raise ValueError('Downloaded result hash differs from retained original receipt')
        except ValueError:
            os.replace(part,part.with_name(part.name+'.corrupt-'+uuid.uuid4().hex[:8]));raise
        os.replace(part,dest)
        if meta.exists():meta.unlink()
    try:r=urllib.request.urlopen(req,timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code==416 and re.fullmatch(r'bytes \*/'+str(start),e.headers.get('Content-Range','')) and start>0:
            if old.get('etag') and e.headers.get('ETag') and old['etag']!=e.headers['ETag']:raise ValueError('Result identity changed')
            finish();return
        raise
    with r:
        cr=r.headers.get('Content-Range','');m=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',cr)
        mode='wb';expected=None;segment=None;etag=r.headers.get('ETag');length=r.headers.get('Content-Length')
        if r.status==206:
            if not m or int(m[1])!=start or int(m[2])<start or int(m[2])>=int(m[3]):raise ValueError('Wrong Content-Range bounds')
            if start and old.get('etag') and etag and old['etag']!=etag:
                os.replace(part,part.with_name(part.name+'.stale-'+uuid.uuid4().hex[:8]));raise ValueError('Result identity changed; download original task again')
            if start and old.get('total') is not None and old['total']!=int(m[3]):raise ValueError('Result length changed')
            mode='ab' if start else 'wb';expected=int(m[3]);segment=int(m[2])-start+1
            if length and int(length)!=segment:raise ValueError('Content-Length disagrees with Content-Range')
        elif r.status==200:
            start=0;expected=int(length) if length else None;segment=expected
        else:raise ValueError('Unexpected download response')
        save(meta,{'etag':etag,'total':expected});received=0
        with part.open(mode) as out:
            while True:
                block=r.read(65536)
                if not block:break
                out.write(block);received+=len(block)
            out.flush();os.fsync(out.fileno())
        if segment is not None and received!=segment:raise ValueError('Incomplete response; resume original task result')
        if expected is not None and part.stat().st_size!=expected:raise ValueError('Incomplete file; resume original task result')
    finish()
