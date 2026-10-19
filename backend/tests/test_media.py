import subprocess

import pytest
from imageio_ffmpeg import get_ffmpeg_exe

from backend.media import compatible_command, subtitle_to_vtt


@pytest.mark.parametrize('extension', ['.srt', '.vtt'])
def test_subtitle_timestamps_encoding_and_seek(extension):
    source = '1\r\n00:00:01,000 --> 00:00:03,500\r\nQué día.\r\n\r\n2\r\n00:00:04,000 --> 00:00:05,000\r\nFin.\r\n'
    if extension == '.vtt':
        source = 'WEBVTT\n\n' + source.replace(',', '.')
    converted = subtitle_to_vtt(source.encode('cp1252'), extension, 2)
    assert converted.startswith('WEBVTT\n\n')
    assert '00:00:00.000 --> 00:00:01.500' in converted
    assert '00:00:02.000 --> 00:00:03.000' in converted
    assert 'Qué día.' in converted
    assert subtitle_to_vtt(source.encode('utf-16'), extension, 6) == 'WEBVTT\n\n\n'


def test_compatible_audio_transcodes_eac3_to_aac(tmp_path):
    source = tmp_path / 'source.mkv'
    target = tmp_path / 'compatible.mp4'
    executable = get_ffmpeg_exe()
    subprocess.run([executable, '-hide_banner', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=8:d=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                    '-c:v', 'libx264', '-threads', '1', '-c:a', 'eac3', '-shortest', str(source)],
                   check=True, capture_output=True, timeout=15)
    command = compatible_command(str(source), 0)
    for option in ('-rw_timeout', '-readrate'):
        index = command.index(option)
        del command[index:index + 2]
    result = subprocess.run(command, check=True, capture_output=True, timeout=15)
    target.write_bytes(result.stdout)
    decoded = subprocess.run([executable, '-hide_banner', '-i', str(target), '-vn',
                              '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, timeout=15)
    diagnostics = decoded.stderr.decode()
    assert decoded.returncode == 0
    assert 'Audio: aac' in diagnostics
    assert 'stereo' in diagnostics
    assert 'mean_volume: -inf' not in diagnostics
    assert 'mean_volume:' in diagnostics


def test_invalid_subtitles_are_not_silently_accepted():
    with pytest.raises(ValueError, match='marcas de tiempo'):
        subtitle_to_vtt(b'This is not a subtitle', '.srt')


def test_loopback_source_keeps_google_credentials_private_and_forwards_ranges(monkeypatch):
    import httpx

    from backend import media

    class VideoBytes(httpx.SyncByteStream):
        def __iter__(self):
            yield b'video'

    def respond(request):
        assert request.headers['authorization'] == 'Bearer private-token'
        assert request.headers['range'] == 'bytes=10-14'
        return httpx.Response(206, headers={'Content-Length': '5', 'Content-Range': 'bytes 10-14/100'},
                              stream=VideoBytes())

    http_client = httpx.Client
    monkeypatch.setattr(media.httpx, 'Client',
                        lambda **options: http_client(transport=httpx.MockTransport(respond), **options))
    source = media.DriveSource('https://www.googleapis.com/drive/v3/files/private?alt=media', 'private-token')
    url = source.start()
    try:
        assert url.startswith('http://127.0.0.1:')
        assert 'private-token' not in url
        assert 'private-token' not in ' '.join(compatible_command(url, 0))
        with http_client() as client:
            response = client.get(url, headers={'Range': 'bytes=10-14'})
            assert response.status_code == 206
            assert response.content == b'video'
            assert response.headers['content-range'] == 'bytes 10-14/100'
            assert client.get(url + '/wrong').status_code == 404
    finally:
        source.close()
