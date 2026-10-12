import os, uuid, json, subprocess, threading, time
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(os.getenv('MOVIERECAP_DATA', './data')).resolve()
UPLOADS, OUTPUTS = ROOT / 'uploads', ROOT / 'outputs'
UPLOADS.mkdir(parents=True, exist_ok=True); OUTPUTS.mkdir(parents=True, exist_ok=True)
BASE_URL = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
API_KEY = os.getenv('MOVIERECAP_API_KEY', '').strip()
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '2048'))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
JOB_TTL_HOURS = int(os.getenv('JOB_TTL_HOURS', '24'))
JOBS = {}; LOCK = threading.Lock()

app = FastAPI(title='MovieRecapAI Processing Server', version='1.9')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])


def auth(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, 'Invalid or missing API key')


def ffmpeg(*args):
    return subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error',*args], capture_output=True, text=True, timeout=6*3600)


def cleanup_old():
    cutoff = time.time() - JOB_TTL_HOURS * 3600
    for p in list(UPLOADS.glob('*')) + list(OUTPUTS.glob('*')):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff: p.unlink()
        except Exception: pass
    with LOCK:
        for jid, job in list(JOBS.items()):
            if job.get('created_at', time.time()) < cutoff: JOBS.pop(jid, None)


def safe_id(value):
    return isinstance(value, str) and len(value) == 32 and all(c in '0123456789abcdef' for c in value)


def normalize_duration(value):
    if value is None: return None
    v=str(value).strip()
    if not v: return None
    try:
        parts=v.split(':')
        if len(parts)==3:
            h,m,sec=map(float,parts); total=h*3600+m*60+sec
        elif len(parts)==2:
            m,sec=map(float,parts); total=m*60+sec
        elif len(parts)==1:
            total=float(parts[0])*60
        else: return None
        return str(total) if total>0 else None
    except Exception: return None


def manifest_options(value):
    if isinstance(value, dict): return value
    if not isinstance(value, str): return {}
    try:
        obj=json.loads(value)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def parse_manifest_text(value):
    # Supports the current v38 human-readable manifest as a compatibility fallback.
    if not isinstance(value, str): return {}
    out={}
    import re
    for key, field in [
        ('voice_language','Voice Language'),('voice_style','Voice Style'),
        ('subtitle_language','Subtitle Language'),('transform','Transform'),
        ('copyright','Copyright')]:
        m=re.search(r'(?im)^'+re.escape(field)+r':\s*(.+)$', value)
        if m: out[key]=m.group(1).strip()
    return out


def color_value(v):
    m={'white':'&H00FFFFFF','yellow':'&H0000FFFF','cyan':'&H00FFFF00','green':'&H0000FF00','red':'&H000000FF'}
    return m.get(str(v).strip().lower(), str(v) if str(v).startswith('&H') else '&H00FFFFFF')


def subtitle_alignment(v):
    m={'bottom':2,'middle':5,'center':5,'top':8}
    return m.get(str(v).strip().lower(), 2)


def request_base_url(request: Request):
    proto=request.headers.get('x-forwarded-proto') or request.url.scheme
    host=request.headers.get('x-forwarded-host') or request.headers.get('host') or request.url.netloc
    return f'{proto}://{host}'.rstrip('/')


@app.get('/capabilities')
def capabilities(x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    return {'ok':True,'version':'1.9','features':['movie_upload','srt_upload','audio_upload','logo_upload','duration_hhmmss','thumbnail','subtitle_style','transform','original_subtitle_blur','blur_position','copyright_risk_check','job_status','final_mp4']}


@app.post('/copyright/check')
async def copyright_check(request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    p=await request.json()
    platform=str(p.get('platform') or 'YouTube').strip()
    rights=str(p.get('rights') or '').strip().lower()
    source=str(p.get('source') or '').strip().lower()
    license_name=str(p.get('license') or '').strip().lower()
    notes=[]
    score=0
    if not rights or rights in ('no','unknown','not sure','မသေချာ'):
        score += 60; notes.append('Rights ownership/permission is not confirmed.')
    elif rights in ('yes','owned','permission','licensed'):
        notes.append('Rights/permission is marked as confirmed.')
    if license_name in ('cc by','cc-by','creative commons attribution'):
        notes.append('Attribution is required for CC BY content.')
        score += 10
    elif license_name and license_name not in ('public domain','owned','all rights reserved with permission'):
        score += 15
    if source in ('movie','film','tv','youtube','tiktok','facebook','unknown'):
        score += 20; notes.append('Third-party audiovisual source may trigger platform copyright systems.')
    score=min(score,100)
    risk='LOW' if score < 30 else ('MEDIUM' if score < 60 else 'HIGH')
    return {'ok':True,'platform':platform,'risk':risk,'score':score,'can_guarantee_no_claim':False,'notes':notes,'message':'Risk assessment only; platform copyright systems make the final decision.'}

@app.get('/health')
def health(x_api_key: str | None = Header(default=None)):
    auth(x_api_key); cleanup_old()
    try:
        r = subprocess.run(['ffmpeg','-version'], capture_output=True, text=True, timeout=10)
        ffok = r.returncode == 0; ver = r.stdout.splitlines()[0] if r.stdout else ''
    except Exception:
        ffok = False; ver = ''
    return {'ok': True, 'service':'MovieRecapAI', 'version':'1.9', 'max_upload_mb':MAX_UPLOAD_MB, 'ffmpeg_ok':ffok, 'ffmpeg_version':ver, 'jobs_in_memory':len(JOBS)}


@app.post('/self-test')
def self_test(x_api_key: str | None = Header(default=None)):
    auth(x_api_key); cleanup_old()
    tid=uuid.uuid4().hex; out=OUTPUTS/f'selftest_{tid}.mp4'
    try:
        r=ffmpeg('-f','lavfi','-i','color=c=black:s=320x180:r=10','-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p',str(out))
        if r.returncode or not out.exists() or out.stat().st_size==0: raise RuntimeError(r.stderr[-2000:] or 'FFmpeg self-test failed')
        return {'ok':True,'test':'ffmpeg','message':'FFmpeg self-test passed','result_url':f'{BASE_URL}/outputs/{out.name}','size_bytes':out.stat().st_size}
    except Exception as e:
        out.unlink(missing_ok=True); raise HTTPException(500, f'FFmpeg self-test failed: {e}')


async def save_body(kind: str, request: Request):
    ext={'movie':'.mp4','srt':'.srt','audio':'.mp3','logo':'.png'}[kind]; uid=uuid.uuid4().hex; p=UPLOADS/f'{uid}{ext}'; size=0
    try:
        with p.open('wb') as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES: raise HTTPException(413, f'Upload exceeds {MAX_UPLOAD_MB} MB limit')
                f.write(chunk)
    except Exception:
        p.unlink(missing_ok=True); raise
    if size == 0: p.unlink(missing_ok=True); raise HTTPException(400, 'Empty upload')
    return uid, p


@app.post('/upload/{kind}')
async def upload(kind: str, request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    if kind not in ('movie','srt','audio','logo'): raise HTTPException(404,'Unsupported upload type')
    uid,path=await save_body(kind,request)
    return JSONResponse({'id':uid,'kind':kind,'filename':path.name})


def process_job(job_id,payload):
    with LOCK: JOBS[job_id].update(status='processing',progress=5,message='Preparing files')
    try:
        movie_id,srt_id,audio_id,logo_id=payload.get('movie_id'),payload.get('srt_id'),payload.get('audio_id'),payload.get('logo_id')
        logo_upload=payload.get('logo_upload')
        if not logo_id and isinstance(logo_upload,dict): logo_id=logo_upload.get('id')
        elif not logo_id and isinstance(logo_upload,str):
            try: logo_id=json.loads(logo_upload).get('id')
            except Exception: pass
        duration=normalize_duration(payload.get('duration'))
        opts=payload.get('options') if isinstance(payload.get('options'),dict) else manifest_options(payload.get('options'))
        if not opts: opts=manifest_options(payload.get('manifest')) or parse_manifest_text(payload.get('manifest'))
        if not duration: duration=normalize_duration(opts.get('duration'))
        if not safe_id(movie_id) or not safe_id(srt_id): raise ValueError('Valid movie_id and srt_id are required')
        movie=next(UPLOADS.glob(movie_id+'.*'),None); srt=next(UPLOADS.glob(srt_id+'.*'),None)
        audio=next(UPLOADS.glob(audio_id+'.*'),None) if safe_id(audio_id) else None
        logo=next(UPLOADS.glob(logo_id+'.*'),None) if safe_id(logo_id) else None
        if not movie or not srt: raise ValueError('Uploaded movie/SRT file not found')
        out=OUTPUTS/f'{job_id}.mp4'
        with LOCK: JOBS[job_id].update(progress=20,message='Processing video')
        sp=str(srt).replace('\\','/').replace(':','\\:').replace("'","\\'")
        try: fontsize=int(float(opts.get('subtitle_font_size',opts.get('font_size',48))))
        except Exception: fontsize=48
        fontsize=max(12,min(fontsize,96)); sc=opts.get('subtitle_color','white'); pos=opts.get('subtitle_position','bottom')
        force=f"Fontsize={fontsize},PrimaryColour={color_value(sc)},OutlineColour=&H00000000,Outline=2,Alignment={subtitle_alignment(pos)}"
        graph=[]; current='[0:v]'
        blur=str(opts.get('original_subtitle_blur','')).strip().lower()
        frac=max(0.08,min(float(opts.get('blur_height',0.22) or 0.22),0.5))
        strength=max(1,min(float(opts.get('blur_strength',10) or 10),50))
        blur_pos=str(opts.get('blur_position','bottom')).strip().lower()
        crop_y_expr={'top':'0','middle':f'(ih-ih*{frac})/2','center':f'(ih-ih*{frac})/2','bottom':f'ih*(1-{frac})'}.get(blur_pos,f'ih*(1-{frac})')
        overlay_y_expr={'top':'0','middle':f'(H-H*{frac})/2','center':f'(H-H*{frac})/2','bottom':f'H*(1-{frac})'}.get(blur_pos,f'H*(1-{frac})')
        if blur=='cover':
            graph.append(f"[0:v]drawbox=x=0:y={crop_y_expr}:w=iw:h=ih*{frac}:color=black@0.72:t=fill[stage0]"); current='[stage0]'
        elif blur=='blur':
            graph.append('[0:v]split=2[main][blursrc]')
            graph.append(f"[blursrc]crop=iw:ih*{frac}:0:{crop_y_expr},boxblur={strength}[blurband]")
            graph.append(f'[main][blurband]overlay=0:{overlay_y_expr}[stage0]'); current='[stage0]'
        # transformation and new subtitles are applied after original-subtitle handling
        rotate=str(opts.get('rotate','')).strip().lower(); transforms=[]
        if rotate in ('90','right','90 right'): transforms.append('transpose=1')
        elif rotate in ('270','left','90 left'): transforms.append('transpose=2')
        elif rotate=='180': transforms.extend(['transpose=1','transpose=1'])
        flip=str(opts.get('flip','')).strip().lower()
        if flip in ('horizontal','hflip'): transforms.append('hflip')
        elif flip in ('vertical','vflip'): transforms.append('vflip')
        transforms.append(f"subtitles='{sp}':force_style='{force}'")
        graph.append(current+','.join(transforms)+'[vmain]')
        cmd=['-i',str(movie)]
        audio_input_index=None
        if logo:
            logo_pos=str(opts.get('logo_position','top-right')).strip().lower()
            xy={'top-left':'20:20','top-right':'W-w-20:20','bottom-left':'20:H-h-20','bottom-right':'W-w-20:H-h-20','center':'(W-w)/2:(H-h)/2'}.get(logo_pos,'W-w-20:20')
            try: opacity=max(0.05,min(float(opts.get('logo_opacity',100) or 100)/100.0,1))
            except Exception: opacity=1.0
            graph.append(f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[logo]")
            graph.append(f"[vmain][logo]overlay={xy}[vout]")
            cmd += ['-i',str(logo)]
        else:
            graph[-1]=graph[-1].replace('[vmain]','[vout]')
        if audio:
            audio_input_index = 2 if logo else 1
            cmd += ['-i',str(audio)]
        cmd += ['-filter_complex',';'.join(graph),'-map','[vout]']
        if audio:
            cmd += ['-map',f'{audio_input_index}:a:0','-c:a','aac','-shortest']
        else:
            cmd += ['-map','0:a?','-c:a','aac']
        cmd += ['-c:v','libx264','-preset','veryfast','-crf','20']
        if duration: cmd += ['-t',duration]
        cmd += [str(out)]
        r=ffmpeg(*cmd)
        if r.returncode: raise RuntimeError(r.stderr[-4000:] or 'FFmpeg processing failed')
        with LOCK: JOBS[job_id].update(status='completed',progress=100,message='Completed',result_url=f"{payload.get('public_base_url') or BASE_URL}/outputs/{out.name}")
    except Exception as e:
        with LOCK: JOBS[job_id].update(status='failed',progress=100,message=str(e))


@app.post('/thumbnail')
async def make_thumbnail(request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key); cleanup_old()
    payload = await request.json()
    movie_upload = payload.get('movie_upload')
    title = str(payload.get('title') or 'Movie Recap')[:120]
    if isinstance(movie_upload, str):
        try: movie_upload = json.loads(movie_upload)
        except Exception: pass
    if not isinstance(movie_upload, dict) or not safe_id(movie_upload.get('id')):
        raise HTTPException(400, 'Valid movie_upload response is required')
    movie_id = movie_upload['id']
    movie = next(UPLOADS.glob(movie_id + '.*'), None)
    if not movie: raise HTTPException(404, 'Uploaded movie file not found')
    tid = uuid.uuid4().hex
    txt = UPLOADS / f'{tid}.txt'
    out = OUTPUTS / f'thumbnail_{tid}.jpg'
    txt.write_text(title.replace('\n',' ')[:120], encoding='utf-8')
    font_candidates = [
        '/usr/share/fonts/truetype/noto/NotoSansMyanmar-Regular.ttf',
        '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    ]
    font = next((f for f in font_candidates if Path(f).exists()), None)
    if not font: raise HTTPException(500, 'Thumbnail font not found')
    vf = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,drawtext=fontfile='" + font + "':textfile='" + str(txt).replace('\\','/').replace(':','\\:').replace("'","\\'") + "':fontcolor=white:fontsize=58:box=1:boxcolor=black@0.55:boxborderw=22:x=(w-text_w)/2:y=h-text_h-55"
    try:
        r = ffmpeg('-i', str(movie), '-vf', vf, '-frames:v', '1', '-q:v', '2', str(out))
        if r.returncode or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(r.stderr[-2000:] or 'Thumbnail generation failed')
        return PlainTextResponse(f'{request_base_url(request)}/outputs/{out.name}')
    except Exception as e:
        out.unlink(missing_ok=True)
        raise HTTPException(500, f'Thumbnail generation failed: {e}')
    finally:
        txt.unlink(missing_ok=True)


@app.post('/jobs/from-uploads',status_code=202)
async def create_job_from_uploads(request:Request,x_api_key: str | None = Header(default=None)):
    auth(x_api_key); cleanup_old(); payload=await request.json()
    def rid(v,k):
        if isinstance(v,str):
            try: v=json.loads(v)
            except Exception: pass
        if not isinstance(v,dict) or not safe_id(v.get('id')): raise HTTPException(400,f'Invalid {k} upload response')
        return v['id']
    movie_id=rid(payload.get('movie_upload'),'movie'); srt_id=rid(payload.get('srt_upload'),'srt'); av=payload.get('audio_upload'); audio_id=rid(av,'audio') if av not in (None,'','null') else None
    jid=uuid.uuid4().hex
    with LOCK: JOBS[jid]={'job_id':jid,'status':'queued','progress':0,'message':'Queued','result_url':None,'created_at':time.time()}
    threading.Thread(target=process_job,args=(jid,{'movie_id':movie_id,'srt_id':srt_id,'audio_id':audio_id,'duration':payload.get('duration'),'manifest':payload.get('manifest'),'options':payload.get('options'),'logo_id':payload.get('logo_id'),'logo_upload':payload.get('logo_upload'),'public_base_url':request_base_url(request)}),daemon=True).start()
    return {'job_id':jid,'status':'queued','progress':0,'message':'Job accepted'}


@app.get('/jobs/{job_id}')
def job_status(job_id:str,x_api_key: str | None = Header(default=None)):
    auth(x_api_key)
    with LOCK:
        job=JOBS.get(job_id)
        if not job: raise HTTPException(404,'Job not found')
        return {k:v for k,v in job.items() if k != 'created_at'}


@app.get('/outputs/{filename}')
def output_file(filename:str,x_api_key: str | None = Header(default=None)):
    auth(x_api_key); p=OUTPUTS/filename
    if not p.exists() or p.parent != OUTPUTS: raise HTTPException(404,'Output not found')
    
    media='image/jpeg' if p.suffix.lower() in ('.jpg','.jpeg') else ('image/png' if p.suffix.lower()=='.png' else 'video/mp4')
    return FileResponse(p,media_type=media,filename=p.name)
