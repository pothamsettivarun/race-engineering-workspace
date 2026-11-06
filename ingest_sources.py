"""Import the user-supplied Corvette manual and public YouTube captions.

Run manually: python ingest_sources.py. Originals and the import manifest stay
in data/sources; inaccessible captions are recorded, never invented.
"""
from __future__ import annotations
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request, parse

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / 'data' / 'source-tools'
if TOOLS.exists():
    sys.path.insert(0, str(TOOLS))
import fitz
from youtube_transcript_api import YouTubeTranscriptApi
import requests
import server

MANUAL = 'https://s100.iracing.com/wp-content/uploads/2024/06/Chevrolet-Corvette-Z06-GT3_manual_V2.pdf'
VIDEOS = ['tQrCTRvPmMs', 'ot0Y8Eruc90', 'z7vWsRou5cU']


def get(url):
    req = request.Request(url, headers={'User-Agent': 'Stint/0.2 (personal engineering source import)'})
    with request.urlopen(req, timeout=35) as response:
        return response.read()


def identifier(url):
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def save_source(item):
    item['created_at'] = datetime.now(timezone.utc).isoformat()
    server.atomic_json(server.DATA / 'library' / (item['id'] + '.json'), item)


def import_manual(folder):
    path = folder / 'Corvette-Z06-GT3-manual-V2.pdf'
    if not path.exists():
        data = get(MANUAL)
        if not data.startswith(b'%PDF'):
            raise ValueError('The supplied manual URL did not return a PDF.')
        path.write_bytes(data)
    sections = []
    with fitz.open(path) as doc:
        for i, page in enumerate(doc):
            text = page.get_text('text').strip()
            if text:
                sections.append({'locator': f'Page {i+1}', 'text': text,
                                 'source': MANUAL + f'#page={i+1}'})
        page_count = len(doc)
    if sum(len(s['text']) for s in sections) < 2000:
        raise ValueError('Not enough readable manual text. Visual/OCR review is required.')
    text = '\n\n'.join(f'[{s["locator"]}]\n{s["text"]}' for s in sections)
    item = {'id': identifier(MANUAL), 'title': 'Chevrolet Corvette Z06 GT3.R · iRacing manual V2 (June 2024)',
            'source': MANUAL, 'text': text, 'sections': sections, 'kind': 'manual',
            'car': 'Chevrolet Corvette Z06 GT3.R', 'status': 'imported', 'pages': page_count,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'caveat': 'Published June 2024. Check garage limits and current iRacing build before applying settings. Text extraction does not interpret diagrams.'}
    save_source(item)
    (folder / 'Corvette-manual-extracted.txt').write_text(text, encoding='utf-8')
    # Render the setup discussion for visual inspection; no PDF changes.
    with fitz.open(path) as doc:
        for page_index in [0, min(11, len(doc)-1)]:
            doc[page_index].get_pixmap(matrix=fitz.Matrix(1.2, 1.2)).save(str(folder / f'manual-page-{page_index+1}.png'))
    return {'url': MANUAL, 'status': 'imported', 'title': item['title'], 'pages': page_count, 'characters': len(text)}


def timestamp(seconds):
    return f'{int(seconds)//60:02d}:{int(seconds)%60:02d}'


def import_video(video_id, folder):
    url = 'https://www.youtube.com/watch?v=' + video_id
    title = 'YouTube engineering source · ' + video_id
    try:
        metadata = json.loads(get('https://www.youtube.com/oembed?url=' + parse.quote(url, safe='') + '&format=json'))
        title = metadata.get('title', title)
    except Exception:
        metadata = {}
    try:
        session = requests.Session()
        original_request = session.request
        def timed_request(method, url, **kwargs):
            kwargs.setdefault('timeout', 30)
            return original_request(method, url, **kwargs)
        session.request = timed_request
        api = YouTubeTranscriptApi(http_client=session)
        transcripts = api.list(video_id)
        try:
            transcript = transcripts.find_manually_created_transcript(['en', 'en-US', 'en-GB'])
        except Exception:
            transcript = transcripts.find_generated_transcript(['en', 'en-US', 'en-GB'])
        fetched = transcript.fetch()
        snippets = [{'text': part.text, 'start': part.start, 'duration': part.duration} for part in fetched]
        if not snippets:
            raise ValueError('No caption text returned.')
        sections, chunk, start, chars = [], [], 0, 0
        for part in snippets:
            if not chunk:
                start = part['start']
            chunk.append(f'[{timestamp(part["start"])}] {part["text"]}')
            chars += len(part['text'])
            if chars >= 1800:
                sections.append({'locator': f'{timestamp(start)}–{timestamp(part["start"]+part["duration"])}',
                                 'source': url + f'&t={int(start)}s', 'text': '\n'.join(chunk)})
                chunk, chars = [], 0
        if chunk:
            sections.append({'locator': f'{timestamp(start)} onward', 'source': url + f'&t={int(start)}s', 'text': '\n'.join(chunk)})
        text = '\n'.join(f'[{timestamp(p["start"])}] {p["text"]}' for p in snippets)
        item = {'id': identifier(url), 'title': title, 'source': url, 'text': text, 'sections': sections,
                'kind': 'video transcript', 'status': 'imported', 'language': transcript.language_code,
                'generated_captions': transcript.is_generated,
                'caveat': 'Caption text only; visual demonstrations are not analyzed. Automatically generated captions may misrecognize engineering terms.'}
        save_source(item)
        (folder / (video_id + '.transcript.txt')).write_text(text, encoding='utf-8')
        server.atomic_json(folder / (video_id + '.captions.json'), snippets)
        return {'url': url, 'title': title, 'status': 'imported', 'characters': len(text), 'generated_captions': transcript.is_generated}
    except Exception as exc:
        # Keep the source discoverable in the library, but never retrieve an
        # inaccessible video's title/URL as if its contents were learned.
        reason = type(exc).__name__
        item = {'id': identifier(url), 'title': title, 'source': url, 'text': '', 'sections': [],
                'kind': 'video transcript', 'status': 'pending transcript',
                'caveat': f'Captions could not be imported ({reason}). No video contents are available to the engineer.'}
        save_source(item)
        return {'url': url, 'title': title, 'status': 'pending transcript', 'reason': reason}


def main():
    folder = ROOT / 'data' / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    try:
        results.append(import_manual(folder))
    except Exception as exc:
        results.append({'url': MANUAL, 'status': 'failed', 'reason': str(exc)})
    for video in VIDEOS:
        result = import_video(video, folder)
        results.append(result)
        print(json.dumps(result), flush=True)
    server.atomic_json(folder / 'import-manifest.json', {'sources': results, 'imported_at': datetime.now(timezone.utc).isoformat()})
    profile = {'title': 'Indy 8h · Corvette Z06 GT3.R', 'car': 'Chevrolet Corvette Z06 GT3.R',
               'car_match': ['corvette', 'z06'], 'track': 'Indianapolis Motor Speedway · road course',
               'track_id': '448', 'configuration': 'Road Course',
               'objective': 'endurance', 'vision': 'Prepare an endurance setup for the Indy 8-hour: predictable braking, consistent traction, sustained stint pace, and manageable balance as fuel burns off.',
               'test_plan': 'Baseline: outlap plus 4–6 clean timed laps. Record setup, fuel, tire age, conditions, and corner/phase feedback. Test one change under comparable conditions; validate the candidate over a representative stint.',
               'source_manifest': str(folder / 'import-manifest.json'),
               'caveat': 'Event weather, stint length, driver preferences, and current BoP are not assumed.'}
    server.atomic_json(server.DATA / 'project.json', profile)
    print(json.dumps({'sources': results}, indent=2), flush=True)


if __name__ == '__main__':
    main()
