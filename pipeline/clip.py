#!/usr/bin/env python3
"""NKBN clipper: long video -> 9:16 short -> public GitHub media -> Buffer.

Commands:
  probe  <src>                    scene-change timestamps (clip candidates)
  fetch  <url>                    yt-dlp download into clipper/sources (outside git)
  cut    <src> -s START -e END --slug NAME [--mode blur|crop] [--mute]
  push   <slug>                    commit + push clips/<slug> to nkbn-clips
  post   <slug> --title T [--desc D] [--category 24] [--now] Buffer -> YouTube

START/END: seconds or HH:MM:SS. Raw URL pattern:
  https://raw.githubusercontent.com/Nicola1123/nkbn-clips/main/clips/<slug>/<slug>.mp4
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request

ROOT = '/home/acer/ultron_workspace/nkbn-clips'
SRC_DIR = '/home/acer/ultron_workspace/clipper/sources'
CLIPS = os.path.join(ROOT, 'clips')
PAT_FILE = '/home/acer/ultron_workspace/.secrets/github_pat.txt'
OC_JSON = '/home/acer/nkbn-openclaw-data/openclaw.json'
YT_CHANNEL = '69fe9e315c4c051afa277563'
TT_CHANNEL = '6ad22ef5778b240e2f29b36a'
RAW = 'https://raw.githubusercontent.com/Nicola1123/nkbn-clips/main/clips/'


def sh(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError('%s failed: %s' % (cmd[0], p.stderr[-700:]))
    return p


def to_sec(s):
    if re.match(r'^[0-9.]+$', str(s)):
        return float(s)
    parts = [float(x) for x in str(s).split(':')]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def probe(src):
    p = subprocess.run(['ffmpeg', '-i', src, '-vf', "select='gt(scene,0.35)',showinfo",
                        '-f', 'null', '-'], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    times = re.findall(r'pts_time:([0-9.]+)', p.stderr)
    seen = []
    for t in times:
        v = round(float(t), 1)
        if not seen or v - seen[-1] > 1.0:
            seen.append(v)
    print('scene changes (%d):' % len(seen))
    print(', '.join(str(v) for v in seen[:60]))



def fetch(url):
    os.makedirs(SRC_DIR, exist_ok=True)
    sh(['python3', '-m', 'yt_dlp', '-f',
        'bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b',
        '--no-playlist', '-o', os.path.join(SRC_DIR, '%(id)s.%(ext)s'), url])
    out = sorted((os.path.join(SRC_DIR, f) for f in os.listdir(SRC_DIR)
                  if f.endswith('.mp4')), key=os.path.getmtime)
    print('downloaded:', out[-1] if out else 'NONE')


def cut(src, start, end, slug, mode='blur', mute=False):
    if not re.match(r'^[a-z0-9-]{3,60}$', slug):
        raise RuntimeError('slug must be [a-z0-9-], 3-60 chars')
    s, e = to_sec(start), to_sec(end)
    dur = e - s
    if dur <= 1 or dur > 180:
        raise RuntimeError('clip length %.1fs out of range (1-180s)' % dur)
    d = os.path.join(CLIPS, slug)
    os.makedirs(d, exist_ok=True)
    out = os.path.join(d, slug + '.mp4')
    loud = 'loudnorm=I=-14:TP=-1.5:LRA=11'
    bg = '[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40[bg];[0:v]scale=1080:-2,setsar=1[main];[bg][main]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]'
    if mode == 'crop':
        cmd = ['ffmpeg', '-y', '-ss', '%.3f' % s, '-t', '%.3f' % dur, '-i', src,
               '-af', loud, '-vf', 'scale=-2:1920,crop=1080:1920,format=yuv420p',
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
               '-c:a', 'aac', '-b:a', '160k', '-r', '30', '-movflags', '+faststart']
    else:
        cmd = ['ffmpeg', '-y', '-ss', '%.3f' % s, '-t', '%.3f' % dur, '-i', src,
               '-filter_complex', bg, '-map', '[v]', '-af', loud,
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21',
               '-c:a', 'aac', '-b:a', '160k', '-r', '30', '-movflags', '+faststart']
    if mute:
        cmd = [c for c in cmd if c != loud] + ['-an']
    cmd.append(out)
    sh(cmd)
    sh(['ffmpeg', '-y', '-ss', '%.3f' % (s + 0.5), '-i', src, '-frames:v', '1',
        '-vf', 'scale=1080:1920', '-q:v', '3', os.path.join(d, slug + '-cover.jpg')])
    with open(os.path.join(d, 'source.txt'), 'w') as fh:
        fh.write('origin: ' + os.path.abspath(src) + chr(10))
        fh.write('start: ' + str(start) + chr(10))
        fh.write('end: ' + str(end) + chr(10))
        fh.write('mode: ' + mode + chr(10))
        fh.write('rendered: ' + time.strftime('%Y-%m-%d %H:%M') + chr(10))
    print(json.dumps({'clip': out, 'seconds': round(dur, 1),
                      'bytes': os.path.getsize(out)}))



def push(slug):
    pat = open(PAT_FILE).read().strip()
    d = os.path.join(CLIPS, slug)
    if not os.path.isdir(d):
        raise RuntimeError('no such clip: ' + slug)
    sh(['git', 'add', 'clips/' + slug], cwd=ROOT)
    dirty = subprocess.run(['git', 'status', '--porcelain', 'clips/' + slug], cwd=ROOT,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL).stdout.strip()
    if dirty:
        sh(['git', 'commit', '-m', 'clip: ' + slug], cwd=ROOT)
        last = None
        for _ in range(4):
            p = subprocess.run(['git', 'push', 'origin', 'main'], cwd=ROOT,
                               capture_output=True, text=True,
                               stdin=subprocess.DEVNULL)
            if p.returncode == 0:
                last = None
                break
            last = p.stderr[-300:]
            time.sleep(3)
        if last:
            raise RuntimeError('git push failed: ' + last)
    else:
        print('nothing new to push')
    print(RAW + slug + '/' + slug + '.mp4')


def _mcp(payload, token):
    req = urllib.request.Request(
        'https://mcp.buffer.com/mcp', data=json.dumps(payload).encode(),
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/json',
                 'Accept': 'application/json, text/event-stream'})
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = resp.read().decode()
    doc = None
    try:
        doc = json.loads(body)
    except Exception:
        for ln in body.splitlines():
            if ln.startswith('data: '):
                try:
                    doc = json.loads(ln[6:])
                    break
                except Exception:
                    pass
    if not doc:
        return None, body[:200]
    txt = doc.get('result', {}).get('content', [{}])[0].get('text', '{}')
    try:
        inner = json.loads(txt)
    except Exception:
        return None, txt[:200]
    return inner.get('id'), inner.get('status')


def post(slug, title, desc='', category='24', now=False, channel='youtube'):
    token = json.load(open(OC_JSON))['mcp']['servers']['buffer']['headers'][
        'Authorization'].split(' ', 1)[1]
    url = RAW + slug + '/' + slug + '.mp4'
    text = title + (chr(10) + chr(10) + desc if desc else '')
    args = {'channelId': YT_CHANNEL if channel == 'youtube' else TT_CHANNEL,
            'schedulingType': 'automatic',
            'mode': 'shareNow' if now else 'addToQueue',
            'text': text, 'assets': [{'video': {'url': url}}]}
    if channel == 'youtube':
        args['metadata'] = {'youtube': {'title': title[:100],
                                        'categoryId': category,
                                        'privacy': 'public'}}
    pid, status = _mcp({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                        'params': {'name': 'create_post', 'arguments': args}}, token)
    print(json.dumps({'post_id': pid, 'status': status}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sp = sub.add_parser('probe'); sp.add_argument('src')
    f = sub.add_parser('fetch'); f.add_argument('url')
    c = sub.add_parser('cut')
    c.add_argument('src'); c.add_argument('-s', '--start', required=True)
    c.add_argument('-e', '--end', required=True); c.add_argument('--slug', required=True)
    c.add_argument('--mode', default='blur', choices=['blur', 'crop'])
    c.add_argument('--mute', action='store_true')
    p = sub.add_parser('push'); p.add_argument('slug')
    o = sub.add_parser('post')
    o.add_argument('slug'); o.add_argument('--title', required=True)
    o.add_argument('--desc', default=''); o.add_argument('--category', default='24')
    o.add_argument('--now', action='store_true')
    o.add_argument('--channel', default='youtube', choices=['youtube', 'tiktok'])
    a = ap.parse_args()
    if a.cmd == 'probe': probe(a.src)
    elif a.cmd == 'fetch': fetch(a.url)
    elif a.cmd == 'cut': cut(a.src, a.start, a.end, a.slug, a.mode, a.mute)
    elif a.cmd == 'push': push(a.slug)
    elif a.cmd == 'post': post(a.slug, a.title, a.desc, a.category, a.now, a.channel)


if __name__ == '__main__':
    main()
