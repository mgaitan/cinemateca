from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from tizen.build import build


def test_widget_contains_only_app_assets_and_a_valid_entrypoint(tmp_path):
    target = build(tmp_path / 'cinemateca.wgt')
    with ZipFile(target) as widget:
        assert set(widget.namelist()) == {'config.xml', 'index.html', 'app.js', 'style.css'}
        config = ElementTree.fromstring(widget.read('config.xml'))
        namespaces = {'widget': 'http://www.w3.org/ns/widgets', 'tizen': 'http://tizen.org/ns/widgets'}
        entrypoint = config.find('widget:content', namespaces).attrib['src']
        assert entrypoint in widget.namelist()
        application = config.find('tizen:application', namespaces).attrib
        assert len(application['package']) == 10
        assert application['id'].startswith(application['package'] + '.')
        assert config.find('tizen:profile', namespaces).attrib['name'] == 'tv-samsung'
        assert b'webapis/webapis.js' in widget.read(entrypoint)
    assert target.is_file()
    assert target.parent == tmp_path


def test_widget_build_does_not_bundle_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path('.env').write_text('LOCAL_SECRET=never-package-this')
    target = build(tmp_path / 'cinemateca.wgt')
    with ZipFile(target) as widget:
        assert all(b'never-package-this' not in widget.read(name) for name in widget.namelist())
