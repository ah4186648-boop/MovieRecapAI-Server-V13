import importlib.util
import os
from pathlib import Path
from fastapi.testclient import TestClient

SERVER_PATH = Path(__file__).resolve().parents[1] / 'server.py'
spec = importlib.util.spec_from_file_location('movierecap_server_test', SERVER_PATH)
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
client = TestClient(server.app)


def test_health_and_capabilities():
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json()['ok'] is True
    assert r.json()['ffmpeg_ok'] is True
    c = client.get('/capabilities')
    assert c.status_code == 200
    features = c.json()['features']
    assert 'long_tts_mp3' in features
    assert 'burmese_recap_script' in features


def test_missing_ai_key_fails_cleanly(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    r1 = client.post('/recap/script', json={'source_text': 'စမ်းသပ်စာသား'})
    r2 = client.post('/tts/long', json={'text': 'စမ်းသပ်စာသား'})
    assert r1.status_code == 503
    assert r2.status_code == 503
    assert 'OPENAI_API_KEY' in r1.json()['detail']
    assert 'OPENAI_API_KEY' in r2.json()['detail']


def test_audio_output_routes_and_mime_types():
    outdir = server.OUTPUTS
    names = ['speech_' + 'a'*32 + '.mp3', 'speech_long_' + 'b'*32 + '.mp3']
    try:
        for name in names:
            (outdir / name).write_bytes(b'fake-mp3')
            r = client.get('/outputs/' + name)
            assert r.status_code == 200
            assert r.headers['content-type'].startswith('audio/mpeg')
        assert client.get('/outputs/../../server.py').status_code == 404
        assert client.get('/outputs/unknown.mp3').status_code == 404
    finally:
        for name in names:
            (outdir / name).unlink(missing_ok=True)


def test_bad_input_validation_without_ai_key(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    assert client.post('/recap/script', json={}).status_code == 400
    assert client.post('/tts/long', json={'text': ''}).status_code == 400
    assert client.post('/tts/long', json={'text': 'abc', 'target_minutes': 20}).status_code == 400


def test_video_transcription_validation(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    r = client.post('/recap/transcribe-video', content=b'video', headers={'X-Filename':'movie.mp4'})
    assert r.status_code == 503
    monkeypatch.setenv('OPENAI_API_KEY', 'test-placeholder')
    r2 = client.post('/recap/transcribe-video', content=b'video', headers={'X-Filename':'movie.txt'})
    assert r2.status_code == 400
    r3 = client.post('/recap/transcribe-video', content=b'', headers={'X-Filename':'movie.mp4'})
    assert r3.status_code == 400
