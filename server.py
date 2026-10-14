import os, uuid, json, subprocess, threading, time, re, shutil
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

app = FastAPI(title='MovieRecapAI Processing Server', version='1.13')
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
    return {'ok':True,'version':'1.14','features':['movie_upload','srt_upload','burmese_recap_script','audio_upload','logo_upload','duration_hhmmss','thumbnail','subtitle_style','transform','original_subtitle_blur','blur_position','copyright_risk_check','job_status','final_mp4','single_tts_mp3','long_tts_mp3','actual_audio_duration','openai_recap_script','video_audio_transcription']}


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

@app.post('/recap/script')
async def generate_burmese_recap_script(request: Request, x_api_key: str | None = Header(default=None)):
    """Generate Burmese-only recap narration from a provided transcript/subtitle text."""
    auth(x_api_key)
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not api_key:
        raise HTTPException(503, 'AI service is not configured: set OPENAI_API_KEY on the server')
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, 'JSON body required')
    source_text = str(payload.get('source_text') or payload.get('transcript') or payload.get('text') or '').strip()
    if not source_text:
        raise HTTPException(400, 'source_text is required; provide extracted subtitles or a transcript')
    if len(source_text) > 60000:
        raise HTTPException(413, 'Source text is too long; maximum is 60000 characters')
    try:
        target_minutes = float(payload.get('target_minutes', 52.5))
    except Exception:
        target_minutes = 52.5
    if not 50 <= target_minutes <= 55:
        raise HTTPException(400, 'target_minutes must be between 50 and 55')
    style = str(payload.get('style') or 'natural').strip().lower()
    if style not in {'natural','sad','happy','scary','funny','serious'}:
        style = 'natural'
    # Split at line boundaries where possible; each segment is processed in order.
    parts=[]; remaining=source_text
    while remaining:
        if len(remaining) <= 8500:
            parts.append(remaining.strip()); break
        cut=max(remaining.rfind('\n',0,8500), remaining.rfind('။',0,8500), remaining.rfind('.',0,8500))
        if cut < 4500: cut=8500
        else: cut += 1
        part=remaining[:cut].strip()
        if part: parts.append(part)
        remaining=remaining[cut:].strip()
    if len(parts) > 8:
        raise HTTPException(413, 'Source creates too many sections; provide shorter or cleaner subtitle text')
    style_notes={
        'natural':'သဘာဝကျပြီး စကားပြောသလို ချောမွေ့စွာရေးပါ။',
        'sad':'ဝမ်းနည်းဖွယ်ခန်းများတွင် နူးညံ့လေးနက်သောခံစားချက် ထည့်ပါ။',
        'happy':'ပျော်ရွှင်ဖွယ်ခန်းများတွင် တက်ကြွနွေးထွေးသောအသံနေအသံထားနှင့် ကိုက်ညီအောင်ရေးပါ။',
        'scary':'ကြောက်စရာခန်းများတွင် တင်းမာမှုနှင့် စိတ်လှုပ်ရှားမှုကို သဘာဝကျစွာ တည်ဆောက်ပါ။',
        'funny':'ဟာသခန်းများတွင် အတင်းအကျပ်မဖြစ်သော သဘာဝဟာသအရသာ ထည့်ပါ။',
        'serious':'လေးနက်ပြီး ရုပ်ရှင်ဇာတ်ကြောင်းပြောသူပုံစံဖြင့် ရေးပါ။'
    }
    system_prompt=(
        'သင်သည် မြန်မာဘာသာဖြင့် ရုပ်ရှင်ဇာတ်လမ်းအကျဉ်းချုပ်ပြောပြသူဖြစ်သည်။ '
        'အောက်ပါရင်းမြစ်စာသားကိုသာ အခြေခံပါ။ ဇာတ်ကောင်အမည်များနှင့် အကြောင်းအကျိုးဆက်စပ်မှုကို ထိန်းသိမ်းပါ။ '
        'မသိသောအချက်၊ ဇာတ်ကွက်အသစ်၊ ဇာတ်သိမ်းအသစ် မတီထွင်ပါနှင့်။ '
        'ဇာတ်ကြောင်းစာသားကို မြန်မာဘာသာတစ်မျိုးတည်းဖြင့်သာ ထုတ်ပါ။ အင်္ဂလိပ်ရှင်းလင်းချက်၊ ခေါင်းစဉ်၊ မှတ်ချက်၊ အမှတ်စဉ်များ မထည့်ပါနှင့်။ '
        '“ကျွန်တော်/ကျနော်” ကို ဇာတ်ကြောင်းပြောသူအဖြစ် မသုံးပါနှင့်။ မလိုအပ်ဘဲ “သူတို့/သူဒို့” မသုံးဘဲ ဇာတ်ကောင်အမည်ကို သုံးပါ။ '
        'စကားတိုတို၊ သဘာဝမြန်မာဝါကျ၊ ဖတ်ရလွယ်သော ပုဒ်ဖြတ်ပုဒ်ရပ်များ သုံးပါ။ '
        'ရင်းမြစ်ပိုင်းတစ်ပိုင်းချင်းစီကို ဇာတ်လမ်းအစမှ အဆုံးအထိ အစဉ်လိုက် ဆက်စပ်ရေးပါ။ '
        'အရင်အပိုင်း၏ အကြောင်းအရာကို ထပ်မရေးပါနှင့်။ အပိုင်းတစ်ခုချင်းစီတွင် မိတ်ဆက်နှင့် နိဂုံးချုပ်ကို ထပ်ခါတလဲလဲ မထည့်ပါနှင့်။ '
        'အပိုင်းတိုင်းအတွက် အောက်ပါရင်းမြစ်အတွင်းပါ အရေးကြီးသောဖြစ်ရပ်များကို ချန်မထားဘဲ အကျဉ်းချုပ်ပါ။ '
        'အသံဖတ်ရန် သင့်တော်သော ဇာတ်ကြောင်းစာသားကိုသာ ပြန်ပေးပါ။'
    )
    import urllib.request, urllib.error
    model=os.getenv('OPENAI_RECAP_MODEL','gpt-4o-mini')
    generated=[]
    for idx, part in enumerate(parts,1):
        minutes=target_minutes/len(parts)
        user_prompt=(
            f'ဤသည် ဇာတ်လမ်းရင်းမြစ်၏ အပိုင်း {idx}/{len(parts)} ဖြစ်သည်။ '
            f'ဇာတ်ကြောင်းအသံဖတ်ချိန် စုစုပေါင်း {target_minutes:.1f} မိနစ်ခန့် ရည်မှန်းထားပြီး ဤအပိုင်းအတွက် {minutes:.1f} မိနစ်ခန့်စာ ရေးပါ။ '
            f'{style_notes[style]} ရင်းမြစ်ထဲမပါသော အချက်အလက်ကို မထည့်ပါနှင့်။\n\nရင်းမြစ်စာသား:\n{part}'
        )
        body=json.dumps({'model':model,'messages':[{'role':'system','content':system_prompt},{'role':'user','content':user_prompt}], 'temperature':0.5, 'max_tokens':4000}).encode('utf-8')
        req=urllib.request.Request('https://api.openai.com/v1/chat/completions',data=body,headers={'Authorization':'Bearer '+api_key,'Content-Type':'application/json'},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=240) as response:
                response_obj=json.loads(response.read().decode('utf-8'))
            text_out=str(response_obj['choices'][0]['message']['content'] or '').strip()
            if not text_out:
                raise HTTPException(502, 'AI returned empty narration for section '+str(idx))
            generated.append(text_out)
        except urllib.error.HTTPError as e:
            detail=e.read().decode('utf-8',errors='replace')[:1000]
            raise HTTPException(502,'AI provider error on section '+str(idx)+': '+detail)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502,'AI generation failed on section '+str(idx)+': '+str(e)[:300])
    script='\n\n'.join(generated)
    return {'ok':True,'script':script,'language':'my','style':style,'source_characters':len(source_text),'sections':len(parts),'script_characters':len(script),'target_minutes':target_minutes,'note':'Duration is an estimate. This endpoint requires subtitles/transcript text; it does not transcribe a raw movie file by itself.'}


@app.post('/tts')
async def text_to_speech(request: Request, x_api_key: str | None = Header(default=None)):
    """Generate speech from Burmese narration using a configured OpenAI TTS key."""
    auth(x_api_key)
    speech_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not speech_key:
        raise HTTPException(503, 'Speech service is not configured: set OPENAI_API_KEY on the server')
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, 'JSON body required')
    narration = str(payload.get('text') or '').strip()
    if not narration:
        raise HTTPException(400, 'text is required')
    if len(narration) > 12000:
        raise HTTPException(413, 'Text too long; split narration into chunks of at most 12000 characters')
    style = str(payload.get('style') or 'natural').strip().lower()
    style_instructions = {
        'natural': 'Speak naturally in Burmese, with clear articulation and varied pacing. Do not sound like a flat text reader.',
        'sad': 'Speak Burmese with gentle sadness and restrained emotion, using natural pauses.',
        'happy': 'Speak Burmese warmly and cheerfully, with lively but natural energy.',
        'scary': 'Speak Burmese with suspense, tension, and deliberate pauses. Avoid shouting.',
        'funny': 'Speak Burmese with light, natural comedic timing and playful emphasis.',
        'serious': 'Speak Burmese in a focused, serious cinematic narration style with controlled pacing.'
    }
    instructions = style_instructions.get(style, style_instructions['natural'])
    voice = str(payload.get('voice') or 'alloy').strip()
    allowed_voices = {'alloy','ash','ballad','coral','echo','fable','onyx','nova','sage','shimmer'}
    if voice not in allowed_voices:
        voice = 'alloy'
    import urllib.request, urllib.error
    req_body = json.dumps({'model': os.getenv('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts'), 'voice': voice, 'input': narration, 'instructions': instructions, 'response_format': 'mp3'}).encode('utf-8')
    req = urllib.request.Request('https://api.openai.com/v1/audio/speech', data=req_body, headers={'Authorization': 'Bearer ' + speech_key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            audio_bytes = response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace')[:1200]
        raise HTTPException(502, 'Speech provider error: ' + detail)
    except Exception as e:
        raise HTTPException(502, 'Speech provider connection failed: ' + str(e)[:500])
    if not audio_bytes:
        raise HTTPException(502, 'Speech provider returned empty audio')
    out = OUTPUTS / ('speech_' + uuid.uuid4().hex + '.mp3')
    out.write_bytes(audio_bytes)
    return {'ok': True, 'audio_url': request_base_url(request) + '/outputs/' + out.name, 'format': 'mp3', 'voice': voice, 'style': style, 'characters': len(narration)}


@app.post('/tts/long')
async def text_to_speech_long(request: Request, x_api_key: str | None = Header(default=None)):
    """Chunk long Burmese narration, synthesize each chunk, and join to one MP3."""
    auth(x_api_key)
    speech_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not speech_key:
        raise HTTPException(503, 'Speech service is not configured: set OPENAI_API_KEY on the server')
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, 'JSON body required')

    narration = str(payload.get('text') or '').strip()
    if not narration:
        raise HTTPException(400, 'text is required')
    if len(narration) > 100000:
        raise HTTPException(413, 'Narration is too long; maximum is 100000 characters')

    # Target is advisory: actual duration depends on the amount of text and speaking pace.
    try:
        target_minutes = float(payload.get('target_minutes', 52.5))
    except Exception:
        target_minutes = 52.5
    if not 50 <= target_minutes <= 55:
        raise HTTPException(400, 'target_minutes must be between 50 and 55')

    style = str(payload.get('style') or 'natural').strip().lower()
    style_instructions = {
        'natural': 'Speak naturally in Burmese, with clear articulation and varied pacing. Do not sound like a flat text reader.',
        'sad': 'Speak Burmese with gentle sadness and restrained emotion, using natural pauses.',
        'happy': 'Speak Burmese warmly and cheerfully, with lively but natural energy.',
        'scary': 'Speak Burmese with suspense, tension, and deliberate pauses. Avoid shouting.',
        'funny': 'Speak Burmese with light, natural comedic timing and playful emphasis.',
        'serious': 'Speak Burmese in a focused, serious cinematic narration style with controlled pacing.'
    }
    instructions = style_instructions.get(style, style_instructions['natural'])
    voice = str(payload.get('voice') or 'alloy').strip()
    if voice not in {'alloy','ash','ballad','coral','echo','fable','onyx','nova','sage','shimmer'}:
        voice = 'alloy'

    # Split at sentence boundaries when possible; keep chunks under the provider input limit.
    chunks = []
    remaining = narration
    while remaining:
        if len(remaining) <= 3500:
            chunks.append(remaining.strip())
            break
        cut = max(remaining.rfind(mark, 0, 3500) for mark in ('။', '!', '?', '.', '၊', '\n'))
        if cut < 1500:
            cut = 3500
        else:
            cut += 1
        piece = remaining[:cut].strip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].strip()
    if len(chunks) > 40:
        raise HTTPException(413, 'Too many narration chunks; split into shorter sections')

    import urllib.request, urllib.error
    temp_dir = OUTPUTS / ('tts_parts_' + uuid.uuid4().hex)
    temp_dir.mkdir(parents=True, exist_ok=True)
    part_paths = []
    try:
        for idx, chunk in enumerate(chunks, 1):
            body = json.dumps({
                'model': os.getenv('OPENAI_TTS_MODEL', 'gpt-4o-mini-tts'),
                'voice': voice,
                'input': chunk,
                'instructions': instructions,
                'response_format': 'mp3'
            }).encode('utf-8')
            req = urllib.request.Request(
                'https://api.openai.com/v1/audio/speech',
                data=body,
                headers={'Authorization': 'Bearer ' + speech_key, 'Content-Type': 'application/json'},
                method='POST'
            )
            try:
                with urllib.request.urlopen(req, timeout=240) as response:
                    audio_bytes = response.read()
            except urllib.error.HTTPError as e:
                detail = e.read().decode('utf-8', errors='replace')[:1000]
                raise HTTPException(502, 'Speech provider error on chunk ' + str(idx) + ': ' + detail)
            except Exception as e:
                raise HTTPException(502, 'Speech provider connection failed on chunk ' + str(idx) + ': ' + str(e)[:300])
            part = temp_dir / ('part_%03d.mp3' % idx)
            part.write_bytes(audio_bytes)
            part_paths.append(part)

        concat_file = temp_dir / 'concat.txt'
        concat_file.write_text(''.join("file '" + p.as_posix().replace("'", "'\\''") + "'\n" for p in part_paths), encoding='utf-8')
        joined = OUTPUTS / ('speech_long_' + uuid.uuid4().hex + '.mp3')
        result = ffmpeg('-f', 'concat', '-safe', '0', '-i', str(concat_file), '-c:a', 'libmp3lame', '-q:a', '3', str(joined))
        if result.returncode != 0 or not joined.exists() or joined.stat().st_size == 0:
            raise HTTPException(500, 'Could not combine generated speech audio')
        # Report actual duration. Do not stretch speech unnaturally to fake a 50-minute result.
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(joined)],
            capture_output=True, text=True, timeout=30
        )
        try:
            duration_seconds = float(probe.stdout.strip())
        except Exception:
            duration_seconds = None
        return {
            'ok': True,
            'audio_url': request_base_url(request) + '/outputs/' + joined.name,
            'format': 'mp3',
            'voice': voice,
            'style': style,
            'characters': len(narration),
            'chunks': len(chunks),
            'target_minutes': target_minutes,
            'actual_duration_seconds': duration_seconds,
            'note': 'Audio duration depends on narration length and speaking pace; target_minutes is a requested target, not a guarantee.'
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



@app.post('/recap/transcribe-video')
async def transcribe_video(request: Request, x_api_key: str | None = Header(default=None)):
    """Extract speech from an uploaded foreign movie and return transcript text.

    Send raw video bytes as the request body and set X-Filename to a filename
    ending in .mp4, .mkv, .mov, .webm, or .avi. This endpoint does not generate
    narration; pass the returned transcript to /recap/script.
    """
    auth(x_api_key)
    api_key = os.getenv('OPENAI_API_KEY', '').strip()
    if not api_key:
        raise HTTPException(503, 'AI service is not configured: set OPENAI_API_KEY on the server')
    filename = request.headers.get('x-filename', 'movie.mp4').strip()
    ext = Path(filename).suffix.lower()
    if ext not in {'.mp4', '.mkv', '.mov', '.webm', '.avi', '.m4v'}:
        raise HTTPException(400, 'X-Filename must end in a supported video extension')
    temp_dir = Path(__import__('tempfile').mkdtemp(prefix='recap_transcribe_'))
    video_path = temp_dir / ('source' + ext)
    try:
        size = 0
        with video_path.open('wb') as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f'Video exceeds MAX_UPLOAD_MB ({MAX_UPLOAD_MB} MB)')
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, 'Video request body is empty')
        probe = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(video_path)], capture_output=True, text=True, timeout=60)
        if probe.returncode != 0:
            raise HTTPException(400, 'Could not read this video; check that it is a valid video file')
        try:
            duration = float(probe.stdout.strip())
        except Exception:
            duration = 0
        if duration <= 0:
            raise HTTPException(400, 'Video duration could not be determined')
        # 8-minute segments at 48 kbps are small enough for the transcription API.
        chunk_seconds = 480
        transcript_parts = []
        import urllib.request, urllib.error
        model = os.getenv('OPENAI_TRANSCRIBE_MODEL', 'gpt-4o-mini-transcribe')
        for idx, start in enumerate(range(0, int(duration + 1), chunk_seconds), 1):
            audio_path = temp_dir / f'audio_{idx:03d}.mp3'
            conv = subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(video_path),'-t',str(chunk_seconds),'-vn','-ac','1','-ar','16000','-b:a','48k',str(audio_path)], capture_output=True, text=True, timeout=1800)
            if conv.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
                # A final segment may contain no audio; skip only if it is beyond the media end.
                if start >= duration: continue
                raise HTTPException(502, 'Audio extraction failed for segment '+str(idx)+': '+(conv.stderr[-500:] or 'unknown FFmpeg error'))
            boundary='----MovieRecapAI'+uuid.uuid4().hex
            with audio_path.open('rb') as af:
                audio_bytes=af.read()
            fields = []
            def add_field(name, value):
                fields.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode())
            add_field('model', model)
            add_field('response_format', 'json')
            fields.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\nContent-Type: audio/mpeg\r\n\r\n').encode()+audio_bytes+b'\r\n')
            body=b''.join(fields)+f'--{boundary}--\r\n'.encode()
            req=urllib.request.Request('https://api.openai.com/v1/audio/transcriptions',data=body,headers={'Authorization':'Bearer '+api_key,'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
            try:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    result=json.loads(resp.read().decode('utf-8'))
                part=str(result.get('text') or '').strip()
                if part: transcript_parts.append(part)
            except urllib.error.HTTPError as e:
                detail=e.read().decode('utf-8',errors='replace')[:800]
                raise HTTPException(502, 'Transcription provider error on segment '+str(idx)+': '+detail)
            finally:
                audio_path.unlink(missing_ok=True)
        transcript='\n'.join(transcript_parts).strip()
        if not transcript:
            raise HTTPException(502, 'No speech transcript was returned for this video')
        return {'ok':True,'transcript':transcript,'language':'auto','video_seconds':duration,'video_bytes':size,'segments':len(transcript_parts),'next_endpoint':'/recap/script','note':'Transcript is extracted from audio; review names and dialogue before generating narration.'}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, 'Video transcription failed: '+str(e)[:300])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.get('/health')
def health():
    # Render and other platform health probes cannot attach app API keys.
    # This endpoint deliberately exposes operational status only, never secrets.
    cleanup_old()
    try:
        r = subprocess.run(['ffmpeg','-version'], capture_output=True, text=True, timeout=10)
        ffok = r.returncode == 0; ver = r.stdout.splitlines()[0] if r.stdout else ''
    except Exception:
        ffok = False; ver = ''
    return {'ok': True, 'service':'MovieRecapAI', 'version':'1.14', 'max_upload_mb':MAX_UPLOAD_MB, 'ffmpeg_ok':ffok, 'ffmpeg_version':ver, 'jobs_in_memory':len(JOBS), 'api_key_enabled':bool(API_KEY), 'security_note':None if API_KEY else 'MOVIERECAP_API_KEY is not configured; configure it before public production use.'}


@app.post('/self-test')
def self_test(request: Request, x_api_key: str | None = Header(default=None)):
    auth(x_api_key); cleanup_old()
    tid=uuid.uuid4().hex; out=OUTPUTS/f'selftest_{tid}.mp4'
    try:
        r=ffmpeg('-f','lavfi','-i','color=c=black:s=320x180:r=10','-t','1','-an','-c:v','libx264','-pix_fmt','yuv420p',str(out))
        if r.returncode or not out.exists() or out.stat().st_size==0: raise RuntimeError(r.stderr[-2000:] or 'FFmpeg self-test failed')
        return {'ok':True,'test':'ffmpeg','message':'FFmpeg self-test passed','result_url':f'{request_base_url(request)}/outputs/{out.name}','size_bytes':out.stat().st_size}
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
        if audio_id is not None and not safe_id(audio_id): raise ValueError('Invalid audio upload ID')
        if logo_id is not None and not safe_id(logo_id): raise ValueError('Invalid logo upload ID')
        movie=next(UPLOADS.glob(movie_id+'.*'),None); srt=next(UPLOADS.glob(srt_id+'.*'),None)
        audio=next(UPLOADS.glob(audio_id+'.*'),None) if audio_id else None
        logo=next(UPLOADS.glob(logo_id+'.*'),None) if logo_id else None
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
def output_file(filename:str):
    # Result URLs are opened by Android's external viewer/download action, which
    # cannot reliably attach X-API-Key. Names are UUID-based and non-guessable.
    # Keep this endpoint limited to generated output files and reject path syntax.
    if filename != Path(filename).name or '/' in filename or '\\\\' in filename:
        raise HTTPException(404, 'Output not found')
    # Permit only the exact generated-output naming schemes, including MP3s
    # created by /tts and /tts/long. Never accept arbitrary filesystem names.
    allowed = (
        re.fullmatch(r'[0-9a-f]{32}\.mp4', filename)
        or re.fullmatch(r'thumbnail_[0-9a-f]{32}\.jpg', filename)
        or re.fullmatch(r'selftest_[0-9a-f]{32}\.mp4', filename)
        or re.fullmatch(r'speech_[0-9a-f]{32}\.mp3', filename)
        or re.fullmatch(r'speech_long_[0-9a-f]{32}\.mp3', filename)
    )
    if not allowed:
        raise HTTPException(404, 'Output not found')
    p=OUTPUTS/filename
    if not p.is_file() or p.parent.resolve() != OUTPUTS.resolve(): raise HTTPException(404,'Output not found')
    
    media='image/jpeg' if p.suffix.lower() in ('.jpg','.jpeg') else ('image/png' if p.suffix.lower()=='.png' else ('audio/mpeg' if p.suffix.lower()=='.mp3' else 'video/mp4'))
    return FileResponse(p,media_type=media,filename=p.name)
