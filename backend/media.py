from __future__ import annotations

import re
import subprocess
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from imageio_ffmpeg import get_ffmpeg_exe


class DriveSource:
    def __init__(self, url: str, token: str):
        secret_path = '/' + secrets.token_urlsafe(32)
        client = httpx.Client(timeout=25, follow_redirects=True)

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != secret_path:
                    self.send_error(404)
                    return
                headers = {'Authorization': f'Bearer {token}'}
                if self.headers.get('Range'):
                    headers['Range'] = self.headers['Range']
                try:
                    with client.stream('GET', url, headers=headers) as response:
                        self.send_response(response.status_code)
                        for name in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                            if name in response.headers:
                                self.send_header(name, response.headers[name])
                        self.end_headers()
                        for chunk in response.iter_raw(256 * 1024):
                            self.wfile.write(chunk)
                except (httpx.HTTPError, BrokenPipeError, ConnectionResetError):
                    self.close_connection = True

            def log_message(self, *args):
                pass

        self.client = client
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.url = f'http://127.0.0.1:{self.server.server_port}{secret_path}'

    def start(self):
        self.thread.start()
        return self.url

    def close(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        self.client.close()


def compatible_command(url: str, start: float) -> list[str]:
    return [get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-rw_timeout', '25000000', '-probesize', '1048576', '-analyzeduration', '1000000',
            '-readrate', '1.2', *(['-ss', str(start)] if start else []), '-i', url,
            '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '192k', '-ac', '2', '-sn', '-dn',
            '-avoid_negative_ts', 'make_zero', '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-frag_duration', '1000000', '-f', 'mp4', 'pipe:1']


def subtitle_to_vtt(content: bytes, extension: str, offset: float = 0) -> str:
    try:
        source = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        source = content.decode('utf-16') if content.startswith((b'\xff\xfe', b'\xfe\xff')) else content.decode('cp1252')
    if extension in {'.ass', '.ssa'}:
        result = subprocess.run([get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                                 '-f', 'ass', '-i', 'pipe:0', '-f', 'webvtt', 'pipe:1'],
                                input=source.encode(), capture_output=True, timeout=10, check=True)
        source = result.stdout.decode('utf-8-sig')
    source = source.replace('\r\n', '\n').replace('\r', '\n').strip()
    source = re.sub(r'^WEBVTT[^\n]*(?:\n|$)', '', source).lstrip()
    timestamp = r'(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})'
    timing = re.compile(r'^' + timestamp + r'\s+-->\s+' + timestamp + r'(.*)$', re.MULTILINE)
    if not timing.search(source):
        raise ValueError('El subtítulo no contiene marcas de tiempo válidas')

    def seconds(groups):
        hours, minutes, seconds_value, milliseconds = groups
        return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds_value) + int(milliseconds) / 1000

    def clock(value):
        total = max(0, round(value * 1000))
        hours, remainder = divmod(total, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds_value, milliseconds = divmod(remainder, 1000)
        return f'{hours:02}:{minutes:02}:{seconds_value:02}.{milliseconds:03}'

    cues = []
    for block in re.split(r'\n\s*\n', source):
        match = timing.search(block)
        if not match:
            continue
        start = seconds(match.groups()[:4]) - offset
        end = seconds(match.groups()[4:8]) - offset
        if end <= 0:
            continue
        cues.append(f'{clock(start)} --> {clock(end)}{match.group(9)}\n{block[match.end():].lstrip()}')
    return 'WEBVTT\n\n' + '\n\n'.join(cues) + '\n'
