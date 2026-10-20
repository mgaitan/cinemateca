from pathlib import Path
import json
import shutil
import subprocess
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from backend.media import subtitle_to_vtt
from tizen.build import build


def test_widget_contains_only_app_assets_and_a_valid_entrypoint(tmp_path):
    target = build(tmp_path / 'cinemateca.wgt')
    with ZipFile(target) as widget:
        assert set(widget.namelist()) == {'config.xml', 'index.html', 'app.js', 'subtitles.js', 'style.css'}
        config = ElementTree.fromstring(widget.read('config.xml'))
        namespaces = {'widget': 'http://www.w3.org/ns/widgets', 'tizen': 'http://tizen.org/ns/widgets'}
        entrypoint = config.find('widget:content', namespaces).attrib['src']
        assert entrypoint in widget.namelist()
        application = config.find('tizen:application', namespaces).attrib
        assert len(application['package']) == 10
        assert application['id'].startswith(application['package'] + '.')
        assert config.find('tizen:profile', namespaces).attrib['name'] == 'tv-samsung'
        assert b'webapis/webapis.js' in widget.read(entrypoint)
        assert widget.read(entrypoint).index(b'src="subtitles.js"') < widget.read(entrypoint).index(b'src="app.js"')
    assert target.is_file()
    assert target.parent == tmp_path


def test_widget_build_does_not_bundle_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('.env').write_text('LOCAL_SECRET=never-package-this')
    target = build(tmp_path / 'cinemateca.wgt')
    with ZipFile(target) as widget:
        assert all(b'never-package-this' not in widget.read(name) for name in widget.namelist())


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for the Tizen client')
def test_tizen_webvtt_renders_timed_text_with_overlap_seek_and_safe_markup():
    source = b'1\n00:00:01,000 --> 00:00:03,000\n<i>Hola</i> &amp; adios\n\n2\n00:00:02,000 --> 00:00:04,000\nSegunda linea\notra linea\n'
    script = '''
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {window: {}};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const subtitles = context.window.CinematecaSubtitles;
const cues = subtitles.parse(input.source);
process.stdout.write(JSON.stringify({
  text: input.times.map(time => subtitles.textAt(cues, time)),
  formatted: subtitles.plainText('<script>untrusted</script> &lt;b&gt; &#x1f3ac;'),
  settings: subtitles.textAt(subtitles.parse('WEBVTT\\n\\nNOTE ignore\\ncomment\\n\\nlabel\\n00:02.000 --> 00:03.000 align:end\\nHola'), 2000),
}));
'''
    result = subprocess.run(['node', '-e', script, str(Path('tizen/subtitles.js').resolve())],
                            input=json.dumps({'source': subtitle_to_vtt(source, '.srt'),
                                              'times': [0, 1000, 2500, 3000, 4000, 1500]}),
                            text=True, capture_output=True, check=True)
    data = json.loads(result.stdout)
    assert data['text'] == ['', 'Hola & adios', 'Hola & adios\nSegunda linea\notra linea',
                            'Segunda linea\notra linea', '', 'Hola & adios']
    assert data['formatted'] == 'untrusted <b> 🎬'
    assert data['settings'] == 'Hola'


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js is required for the Tizen client')
def test_tizen_subtitle_playback_native_external_aac_pause_seek_and_cleanup():
    subprocess.run(['node', str(Path(__file__).with_name('tizen_subtitles_client.cjs'))],
                   check=True, capture_output=True, text=True)
