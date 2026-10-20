import json
import secrets
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend import main


@pytest.fixture(params=['sqlite', 'sqlite+libsql'])
def client(tmp_path, monkeypatch, request):
    test_engine = create_engine(f'{request.param}:///{tmp_path / "test.sqlite3"}')
    monkeypatch.setattr(main, 'engine', test_engine)
    monkeypatch.setattr(main, 'APP_ENV', 'development')
    monkeypatch.setattr(main, 'TURSO_DATABASE_URL', '')
    monkeypatch.setattr(main, 'CLIENT_ID', 'test-client')
    monkeypatch.setattr(main, 'CLIENT_SECRET', 'test-secret')
    monkeypatch.setattr(main, 'FERNET_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(main, 'TMDB_TOKEN', '')
    with TestClient(main.app, base_url='https://testserver') as test_client:
        yield test_client


def connect_user(client):
    user_id = secrets.token_urlsafe(20)
    session_id = secrets.token_urlsafe(40)
    with main.db() as connection:
        connection.execute(text('''INSERT INTO sessions
            (sid,user_id,name,email,access_cipher,refresh_cipher,expires_at,created_at)
            VALUES(:sid,:user_id,:name,:email,:access_cipher,:refresh_cipher,:expires_at,:created_at)'''), {
            'sid': session_id, 'user_id': user_id, 'name': 'Test', 'email': 'test@example.com',
            'access_cipher': main.encrypt('access-token'), 'refresh_cipher': main.encrypt('refresh-token'),
            'expires_at': int(time.time()) + 3600, 'created_at': int(time.time()),
        })
    client.cookies.set('cm_sid', session_id)
    return user_id


@pytest.fixture
def drive(monkeypatch):
    async def google_get(url, token, params=None):
        assert token == 'access-token'
        if url == main.DRIVE_URL:
            return {'files': [{
                'id': 'test_movie_12345', 'name': 'Perfect.Days.2023.mp4',
                'mimeType': 'video/mp4', 'size': '1024', 'capabilities': {'canDownload': True},
            }]}
        return {'id': 'test_folder_12345', 'name': 'Compartida', 'mimeType': main.FOLDER_MIME}

    monkeypatch.setattr(main, 'google_get', google_get)


def test_library_sync_edit_and_logout(client, drive):
    user_id = connect_user(client)
    assert client.get('/api/movies').status_code == 404
    assert client.post('/api/library', json={'url': 'test_folder_12345'}).status_code == 200
    assert client.post('/api/sync').json()['count'] == 1
    movie = client.get('/api/movies').json()['movies'][0]
    assert movie['title'] == 'Perfect Days'
    assert client.patch('/api/movies/test_movie_12345', json={'favorite': True}).status_code == 200
    assert client.post('/api/sync?start=true').status_code == 200
    assert client.get('/api/movies').json()['movies'][0]['favorite'] is True
    assert client.get('/api/me').json()['library']['folder_name'] == 'Compartida'
    with main.db() as connection:
        encrypted = connection.execute(text('SELECT access_cipher FROM sessions WHERE user_id=:user_id'),
                                       {'user_id': user_id}).scalar_one()
    assert encrypted != 'access-token'
    assert main.decrypt(encrypted) == 'access-token'
    assert client.post('/auth/logout').status_code == 200
    assert client.get('/api/movies').status_code == 401


def test_library_is_private_per_user(client, drive):
    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    connect_user(client)
    assert client.get('/api/movies').status_code == 404
    assert client.patch('/api/movies/test_movie_12345', json={'favorite': True}).status_code == 404
    assert client.get('/api/stream/test_movie_12345').status_code == 404
    assert client.get('/api/stream/test_movie_12345?download=true').status_code == 404


def test_catalog_pages_groups_and_filters_entire_library(client, drive):
    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    films = [{'id': f'movie_{index:03}_12345', 'title': f'Película {index:03}', 'year': str(2000 + index),
              'genre': 'Drama' if index < 16 else 'Comedia', 'favorite': index == 32,
              'popularity': index, 'director': 'Directora especial' if index == 32 else ''}
             for index in range(33)]
    films.append({**films[0], 'id': 'version_second_12345'})
    with main.db() as connection:
        main.save_movies(connection, user_id, films)
    first = client.get('/api/movies').json()
    assert first['count'] == 33
    assert first['next_offset'] == 16
    assert len(first['movies']) == 17
    assert first['genres'] == ['Comedia', 'Drama']
    second = client.get('/api/movies?offset=16').json()
    third = client.get('/api/movies?offset=32').json()
    assert len(second['movies']) == 16
    assert third['next_offset'] is None
    assert len(third['movies']) == 1
    assert not {movie['id'] for movie in first['movies']} & {movie['id'] for movie in second['movies']}
    for query in ('q=directora%20especial', 'favorite=true', 'q=pelicula%20032'):
        page = client.get('/api/movies?' + query).json()
        assert page['count'] == 1
        assert page['movies'][0]['id'] == films[32]['id']
    assert client.get('/api/movies?genre=Comedia').json()['count'] == 17
    for order in ('year', 'popularity'):
        assert client.get('/api/movies?sort=' + order).json()['movies'][0]['id'] == films[32]['id']
    for query in ('offset=-1', 'limit=0', 'limit=2001', 'sort=invalid'):
        assert client.get('/api/movies?' + query).status_code == 422


def test_movie_details_load_directly_with_versions_and_edits(client, drive):
    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    films = [{'id': f'movie_{index:03}_12345', 'title': f'Película {index:03}', 'year': '2024'}
             for index in range(20)]
    films[-1]['tmdbId'] = 123
    films.append({**films[-1], 'id': 'another_version_12345', 'title': 'Versión alternativa'})
    with main.db() as connection:
        main.save_movies(connection, user_id, films)
    assert films[-2]['id'] not in {film['id'] for film in client.get('/api/movies').json()['movies']}
    response = client.get('/api/movies/' + films[-2]['id'])
    assert response.status_code == 200
    assert response.json()['movie']['id'] == films[-2]['id']
    assert {version['id'] for version in response.json()['versions']} == {films[-1]['id'], films[-2]['id']}
    client.patch('/api/movies/' + films[-2]['id'], json={'overview': 'Edición manual', 'favorite': True})
    assert client.get('/api/movies/' + films[-2]['id']).json()['movie']['overview'] == 'Edición manual'
    assert client.get('/api/movies/unknown_movie_12345').status_code == 404
    assert client.get('/api/movies/invalid').status_code == 400
    client.post('/api/sharing', json={'enabled': True})
    client.post('/auth/logout')
    assert client.get('/api/movies/' + films[-2]['id']).json()['movie']['favorite'] is True
    with main.db() as connection:
        connection.execute(text('UPDATE public_library SET enabled=0'))
    assert client.get('/api/movies/' + films[-2]['id']).status_code == 401
    connect_user(client)
    assert client.get('/api/movies/' + films[-2]['id']).status_code == 404


def test_movie_details_group_versions_by_title_without_tmdb(client, drive):
    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    with main.db() as connection:
        main.save_movies(connection, user_id, [
            {'id': 'movie_first_12345', 'title': 'Antígona', 'year': '2019'},
            {'id': 'movie_second_12345', 'title': 'ANTIGONA', 'year': '2019'},
            {'id': 'movie_remake_12345', 'title': 'Antígona', 'year': '2024'},
        ])
    data = client.get('/api/movies/movie_first_12345').json()
    assert {film['id'] for film in data['versions']} == {'movie_first_12345', 'movie_second_12345'}


def test_catalog_survives_restart_and_does_not_scan_drive(client, drive, monkeypatch):
    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    before = client.get('/api/movies').json()

    async def unexpected_request(*args, **kwargs):
        pytest.fail('Reading the saved library must not contact Drive')

    monkeypatch.setattr(main, 'google_get', unexpected_request)
    main.engine.dispose()
    main.init_db()
    assert client.get('/api/movies').json() == before


@pytest.mark.parametrize('shared', [False, True])
def test_server_resumes_saved_sync_without_browser(client, monkeypatch, shared):
    import asyncio

    connect_user(client)
    requests = []

    async def google_get(url, token, params=None):
        if url != main.DRIVE_URL:
            return {'mimeType': main.FOLDER_MIME, 'name': 'Biblioteca'}
        requests.append(params.get('pageToken'))
        if not params.get('pageToken'):
            return {'files': [{'id': 'movie_first_12345', 'name': 'First.mp4'}], 'nextPageToken': 'next'}
        return {'files': [{'id': 'movie_second_12345', 'name': 'Second.mp4'}]}

    monkeypatch.setattr(main, 'google_get', google_get)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    assert client.post('/api/sync').json()['status'] == 'running'
    if shared:
        assert client.post('/api/sharing', json={'enabled': True}).status_code == 200
        client.post('/auth/logout')
    main.engine.dispose()
    asyncio.run(main.resume_sync_jobs_once())
    saved = client.get('/api/movies').json()
    assert saved['count'] == 2
    assert saved['sync']['status'] == 'completed'
    assert saved['synced_at'] > 0
    asyncio.run(main.resume_sync_jobs_once())
    assert requests == [None, 'next']


def test_server_repairs_numbered_titles_without_rescanning(client, drive, monkeypatch):
    import asyncio
    import httpx

    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    with main.db() as connection:
        main.save_movies(connection, user_id, [{'id': 'test_movie_12345', 'title': '38 Antigone',
                                               'filename': '38. Antigone (2019).mkv', 'year': '2019', 'poster': ''}])
        state = json.loads(connection.execute(text('SELECT state FROM sync_jobs')).scalar_one())
        state['metadata_version'] = main.METADATA_VERSION - 1
        connection.execute(text('UPDATE sync_jobs SET state=:state'), {'state': json.dumps(state)})
    monkeypatch.setattr(main, 'TMDB_TOKEN', 'tmdb-test')
    searches = []

    def respond(request):
        searches.append(request.url.params['query'])
        return httpx.Response(200, json={'results': [{'poster_path': '/antigone.jpg'}]})

    async def unexpected_scan(*args, **kwargs):
        pytest.fail('Metadata repairs must reuse the stored Drive listing')

    monkeypatch.setattr(main, 'google_get', unexpected_scan)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    asyncio.run(main.resume_sync_jobs_once())
    film = client.get('/api/movies').json()['movies'][0]
    assert searches == ['Antigone']
    assert film['title'] == 'Antigone'
    assert film['poster'].endswith('/antigone.jpg')
    client.patch('/api/movies/test_movie_12345', json={'title': 'Mi Antígona'})
    assert client.get('/api/movies').json()['movies'][0]['title'] == 'Mi Antígona'


@pytest.mark.parametrize('download', [False, True])
@pytest.mark.parametrize('casting', [False, True])
def test_video_stream_and_download(client, drive, monkeypatch, download, casting):
    import httpx

    from urllib.parse import quote

    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    filename = 'Película "especial"\r\n2023.mkv'
    with main.db() as connection:
        connection.execute(text('UPDATE movies SET metadata=:metadata WHERE user_id=:user_id'), {
            'user_id': user_id, 'metadata': json.dumps({'filename': filename, 'canDownload': True}),
        })
    closed = []

    class VideoBytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'video'

        async def aclose(self):
            closed.append(True)

    def respond(request):
        assert request.headers['authorization'] == 'Bearer access-token'
        assert request.headers['range'] == 'bytes=0-4'
        assert request.url.params['alt'] == 'media'
        assert request.url.path.endswith('/test_movie_12345')
        return httpx.Response(206, headers={
            'Content-Type': 'video/x-matroska', 'Content-Length': '5', 'Content-Range': 'bytes 0-4/100',
        }, stream=VideoBytes())

    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    params = {'download': str(download).lower()}
    if casting:
        ticket = client.post('/api/movies/test_movie_12345/cast')
        assert ticket.status_code == 200
        assert ticket.headers['cache-control'] == 'no-store'
        params['cast_token'] = ticket.json()['token']
        client.cookies.clear()
    response = client.get('/api/stream/test_movie_12345',
                          params=params, headers={'Range': 'bytes=0-4', 'Origin': 'https://receiver.example'})
    assert response.status_code == 206
    assert response.content == b'video'
    assert response.headers['content-range'] == 'bytes 0-4/100'
    assert response.headers['content-type'] == 'video/x-matroska'
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['access-control-allow-origin'] == '*'
    assert 'Content-Range' in response.headers['access-control-expose-headers']
    if download:
        assert response.headers['content-disposition'] == f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    else:
        assert 'content-disposition' not in response.headers
    assert closed


def test_cast_ticket_scope_expiry_and_revocation(client, drive):
    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    assert client.post('/api/movies/test_movie_12345/cast', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/movies/unknown_movie_12345/cast').status_code == 404
    ticket = client.post('/api/movies/test_movie_12345/cast').json()
    assert ticket['expires_at'] > time.time() + 11 * 3600
    assert 'access-token' not in json.dumps(ticket)
    assert 'refresh-token' not in json.dumps(ticket)
    owner_sid = client.cookies.get('cm_sid')
    client.cookies.clear()
    params = {'cast_token': ticket['token']}
    response = client.head('/api/stream/test_movie_12345', params=params)
    assert response.status_code == 200
    assert response.headers['content-length'] == '1024'
    assert response.headers['content-type'] == 'video/mp4'
    assert client.head('/api/play/test_movie_12345', params=params).status_code == 200
    assert client.get('/api/stream/another_movie_12345', params=params).status_code == 401
    assert client.get('/api/movies', params=params).status_code == 401
    assert client.get('/api/movies/test_movie_12345', params=params).status_code == 401
    assert client.patch('/api/movies/test_movie_12345', params=params, json={'favorite': True}).status_code == 401
    assert client.head('/api/play/test_movie_12345', params={'cast_token': ticket['token'] + 'tampered'}).status_code == 401
    data = main.cipher().decrypt(ticket['token'].encode())
    expired = main.cipher().encrypt_at_time(data, int(time.time()) - main.CAST_TICKET_TTL - 1).decode()
    assert client.head('/api/play/test_movie_12345', params={'cast_token': expired}).status_code == 401
    with main.db() as connection:
        connection.execute(text('UPDATE libraries SET folder_id=:folder WHERE user_id=:user_id'),
                           {'folder': 'new_folder_12345', 'user_id': user_id})
    assert client.head('/api/play/test_movie_12345', params=params).status_code == 401
    with main.db() as connection:
        connection.execute(text('UPDATE libraries SET folder_id=:folder WHERE user_id=:user_id'),
                           {'folder': 'test_folder_12345', 'user_id': user_id})
    client.cookies.set('cm_sid', owner_sid)
    client.post('/auth/logout')
    assert client.head('/api/play/test_movie_12345', params=params).status_code == 401


def test_public_cast_tickets_survive_logout_and_respect_sharing(client, drive):
    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    client.post('/api/sharing', json={'enabled': True})
    client.post('/auth/logout')
    ticket = client.post('/api/movies/test_movie_12345/cast')
    assert ticket.status_code == 200
    params = {'cast_token': ticket.json()['token']}
    assert client.head('/api/stream/test_movie_12345', params=params).status_code == 200
    with main.db() as connection:
        main.save_movies(connection, user_id, [{'id': 'test_movie_12345', 'title': 'Restricted', 'canDownload': False}])
    assert client.head('/api/play/test_movie_12345', params=params).status_code == 403
    assert client.post('/api/movies/test_movie_12345/cast').status_code == 403
    with main.db() as connection:
        connection.execute(text('UPDATE public_library SET enabled=0'))
    assert client.head('/api/stream/test_movie_12345', params=params).status_code == 401
    assert client.post('/api/movies/test_movie_12345/cast').status_code == 401


def test_device_cors_is_limited_to_read_routes(client):
    headers = {'Origin': 'https://receiver.example', 'Access-Control-Request-Method': 'GET',
               'Access-Control-Request-Headers': 'Range'}
    for path in ('/api/movies', '/api/movies/test_movie_12345', '/api/movies/test_movie_12345/subtitles', '/api/stream/test_movie_12345', '/api/play/test_movie_12345',
                 '/api/movies/test_movie_12345/subtitles/test_subtitle_12345'):
        response = client.options(path, headers=headers)
        assert response.status_code == 204
        assert response.headers['access-control-allow-origin'] == '*'
        assert 'Range' in response.headers['access-control-allow-headers']
        assert 'access-control-allow-credentials' not in response.headers
        denied = client.get(path)
        assert denied.status_code == 401
        assert denied.headers['access-control-allow-origin'] == '*'
    for path in ('/api/sharing', '/auth/login', '/api/movies/test_movie_12345/cast'):
        assert 'access-control-allow-origin' not in client.options(path, headers=headers).headers


@pytest.mark.parametrize('shared', [False, True])
def test_cast_receiver_refreshes_google_access_without_cookies(client, drive, monkeypatch, shared):
    import httpx

    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    if shared:
        client.post('/api/sharing', json={'enabled': True})
    ticket = client.post('/api/movies/test_movie_12345/cast').json()['token']
    with main.db() as connection:
        connection.execute(text('UPDATE sessions SET expires_at=0'))
        connection.execute(text('UPDATE public_library SET expires_at=0'))
    client.cookies.clear()
    refreshes = []

    def respond(request):
        assert str(request.url) == main.TOKEN_URL
        assert b'grant_type=refresh_token' in request.content
        refreshes.append(True)
        return httpx.Response(200, json={'access_token': 'refreshed-token', 'expires_in': 3600})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    for path in ('/api/stream/test_movie_12345', '/api/play/test_movie_12345'):
        assert client.head(path, params={'cast_token': ticket}).status_code == 200
    assert refreshes == [True]


def test_download_restrictions(client, drive, monkeypatch):
    assert client.get('/api/stream/test_movie_12345?download=true').status_code == 401
    user_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')

    def unexpected_request(**options):
        raise AssertionError('An invalid download must not contact Google Drive')

    monkeypatch.setattr(main.httpx, 'AsyncClient', unexpected_request)
    assert client.get('/api/stream/test_movie_12345?download=true',
                      headers={'Range': 'bytes=0-1,4-5'}).status_code == 416
    with main.db() as connection:
        connection.execute(text('UPDATE movies SET metadata=:metadata WHERE user_id=:user_id'), {
            'user_id': user_id, 'metadata': json.dumps({'filename': 'restricted.mkv', 'canDownload': False}),
        })
    assert client.get('/api/stream/test_movie_12345?download=true').status_code == 403


def test_public_library_is_read_only_and_survives_logout(client, drive):
    owner_id = connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    assert client.post('/api/sharing', json={'enabled': True},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/sharing', json={'enabled': True}).json() == {'public': True}
    assert client.get('/api/me').json()['can_manage'] is True
    assert client.post('/auth/logout').status_code == 200
    profile = client.get('/api/me').json()
    assert profile['public'] is True
    assert profile['connected'] is False
    assert profile['can_manage'] is False
    assert not {'email', 'name', 'default_folder_url', 'library'} & profile.keys()
    catalog = client.get('/api/movies')
    assert catalog.status_code == 200
    assert catalog.json()['count'] == 1
    assert 'access-token' not in catalog.text
    assert 'refresh-token' not in catalog.text
    assert owner_id not in catalog.text
    for path, payload in [('/api/sharing', {'enabled': False}), ('/api/library', {'url': 'other_folder_12345'}),
                          ('/api/sync', None)]:
        assert client.post(path, json=payload).status_code == 401
    assert client.patch('/api/movies/test_movie_12345', json={'favorite': True}).status_code == 401
    connect_user(client)
    assert client.get('/api/me').json()['can_manage'] is False
    assert client.post('/api/sharing', json={'enabled': True}).status_code == 403
    assert client.post('/api/library', json={'url': 'other_folder_12345'}).status_code == 403
    assert client.post('/api/sync').status_code == 403
    assert client.patch('/api/movies/test_movie_12345', json={'favorite': True}).status_code == 403


def test_public_credentials_refresh_without_browser_session(client, drive, monkeypatch):
    import httpx

    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    client.post('/api/sharing', json={'enabled': True})
    client.post('/auth/logout')
    with main.db() as connection:
        connection.execute(text('UPDATE public_library SET expires_at=0'))

    def respond(request):
        assert str(request.url) == main.TOKEN_URL
        assert b'refresh_token=refresh-token' in request.content
        return httpx.Response(200, json={'access_token': 'new-owner-token', 'expires_in': 3600})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))

    async def google_get(url, token, params=None):
        assert token == 'new-owner-token'
        return {'files': [{'id': 'test_movie_12345', 'name': 'Perfect.Days.2023.mp4'},
                          {'id': 'subtitle_123456', 'name': 'Perfect.Days.2023.es.srt'}]}

    monkeypatch.setattr(main, 'google_get', google_get)
    response = client.get('/api/movies/test_movie_12345/subtitles')
    assert response.status_code == 200
    assert len(response.json()['tracks']) == 1
    with main.db() as connection:
        row = connection.execute(text('SELECT * FROM public_library')).mappings().one()
        assert main.decrypt(row['access_cipher']) == 'new-owner-token'
        assert connection.execute(text('SELECT COUNT(*) FROM sessions')).scalar_one() == 0


def test_sharing_can_be_revoked_and_folder_changes_disable_it(client, drive):
    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    client.post('/api/sharing', json={'enabled': True})
    assert client.post('/api/sharing', json={'enabled': False}).json() == {'public': False}
    owner_cookies = dict(client.cookies)
    client.cookies.clear()
    assert client.get('/api/movies').status_code == 401
    client.cookies.update(owner_cookies)
    client.post('/api/sharing', json={'enabled': True})
    client.post('/api/library', json={'url': 'other_folder_12345'})
    assert client.get('/api/me').json()['public'] is False
    client.cookies.clear()
    assert client.get('/api/movies').status_code == 401


@pytest.mark.parametrize('casting', [False, True])
def test_subtitle_discovery_conversion_and_access_control(client, drive, monkeypatch, casting):
    import httpx

    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')

    async def google_get(url, token, params=None):
        assert token == 'access-token'
        return {'files': [{'id': 'test_movie_12345', 'name': 'Perfect.Days.2023.mp4'},
                          {'id': 'other_movie_12345', 'name': 'Other.Movie.2024.mkv'},
                          {'id': 'subtitle_123456', 'name': 'Perfect.Days.2023.es.srt'},
                          {'id': 'other_sub_12345', 'name': 'Other.Movie.2024.en.srt'}]}

    monkeypatch.setattr(main, 'google_get', google_get)

    def respond(request):
        assert request.url.path.endswith('/subtitle_123456')
        return httpx.Response(200, content='1\r\n00:00:01,000 --> 00:00:03,000\r\nHola, día.\r\n'.encode())

    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    discovered = client.get('/api/movies/test_movie_12345/subtitles', headers={'Origin': 'null'})
    assert discovered.headers['access-control-allow-origin'] == '*'
    assert 'access-control-allow-credentials' not in discovered.headers
    tracks = discovered.json()['tracks']
    assert [track['id'] for track in tracks] == ['subtitle_123456']
    params = {}
    if casting:
        params['cast_token'] = client.post('/api/movies/test_movie_12345/cast').json()['token']
        client.cookies.clear()
    response = client.get(tracks[0]['src'], params=params, headers={'Origin': 'https://receiver.example'})
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/vtt')
    assert response.text.startswith('WEBVTT\n')
    assert '00:00:01.000 --> 00:00:03.000' in response.text
    assert 'Hola, día.' in response.text
    assert response.headers['access-control-allow-origin'] == '*'
    shifted = client.get(tracks[0]['src'], params={**params, 'offset': 2}).text
    head = client.head(tracks[0]['src'], params=params)
    assert head.status_code == 200
    assert head.headers['content-type'].startswith('text/vtt')
    assert '00:00:00.000 --> 00:00:01.000' in shifted
    assert client.get('/api/movies/test_movie_12345/subtitles/other_sub_12345', params=params).status_code == 404
    connect_user(client)
    assert client.get('/api/movies/test_movie_12345/subtitles').status_code == 404
    assert client.get(tracks[0]['src']).status_code == 404


def test_tmdb_details_include_country_director_and_cast(client, monkeypatch):
    import asyncio
    import httpx

    def respond(request):
        if '/search/' in request.url.path:
            return httpx.Response(200, json={'results': [{'id': 123, 'release_date': '2024-01-01'}]})
        assert request.url.path == '/3/movie/123'
        assert request.url.params['append_to_response'] == 'credits'
        return httpx.Response(200, json={
            'production_countries': [{'name': 'Argentina'}],
            'credits': {'crew': [{'name': 'Directora', 'job': 'Director'}, {'name': 'Guionista', 'job': 'Writer'}],
                        'cast': [{'name': f'Actor {index}'} for index in range(10)]},
        })

    async def fetch_metadata():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            movie = {'title': 'Película', 'year': ''}
            entry = await main.enrich(movie, http_client, asyncio.Semaphore(1))
            assert entry['tmdbId'] == 123
            assert entry['country'] == 'Argentina'
            assert entry['director'] == 'Directora'
            assert len(entry['cast']) == 6
            assert entry['metadataVersion'] == main.METADATA_VERSION

    asyncio.run(fetch_metadata())


@pytest.mark.parametrize('casting', [False, True])
def test_compatible_endpoint_access_limits_and_cleanup(client, drive, monkeypatch, casting):
    import asyncio
    import sys

    assert client.get('/api/play/test_movie_12345').status_code == 401
    connect_user(client)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    closed = []

    class Source:
        def __init__(self, url, token):
            assert url.startswith(main.DRIVE_URL)
            assert token == 'access-token'

        def start(self):
            return 'http://127.0.0.1:12345/private-source'

        def close(self):
            closed.append(True)

    def command(url, start):
        assert url == 'http://127.0.0.1:12345/private-source'
        assert start == 0
        return [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"ftyp-mp4-aac")']

    monkeypatch.setattr(main, 'DriveSource', Source)
    monkeypatch.setattr(main, 'compatible_command', command)
    params = {}
    owner_sid = client.cookies.get('cm_sid')
    if casting:
        params['cast_token'] = client.post('/api/movies/test_movie_12345/cast').json()['token']
        client.cookies.clear()
    assert client.head('/api/play/test_movie_12345', params=params).status_code == 200
    assert not closed
    response = client.get('/api/play/test_movie_12345', params=params)
    assert response.status_code == 200
    assert response.content == b'ftyp-mp4-aac'
    assert response.headers['content-type'] == 'video/mp4'
    assert response.headers['access-control-allow-origin'] == '*'
    assert closed == [True]
    assert not main.TRANSCODE_SLOTS.locked()
    client.cookies.set('cm_sid', owner_sid)
    assert client.get('/api/play/test_movie_12345?start=-1').status_code == 422
    monkeypatch.setattr(main, 'TRANSCODE_SLOTS', asyncio.Semaphore(0))
    assert client.get('/api/play/test_movie_12345').status_code == 503
    connect_user(client)
    assert client.get('/api/play/test_movie_12345').status_code == 404


def test_csrf_and_oauth_state(client):
    assert client.post('/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
    response = client.get('/auth/login', follow_redirects=False)
    assert response.status_code == 307
    assert 'code_challenge_method=S256' in response.headers['location']
    assert client.get('/auth/callback?code=fake&state=wrong').status_code == 400


def test_static_frontend_and_api_routing(client):
    if not (main.DIST / 'index.html').is_file():
        pytest.skip('Build the frontend to test static serving')
    assert client.get('/').headers['content-type'].startswith('text/html')
    assert client.get('/demo/arrival.jpg').headers['content-type'] == 'image/jpeg'
    assert client.get('/catalogo/peliculas', headers={'Accept': 'text/html'}).headers['content-type'].startswith('text/html')
    assert client.get('/peliculas/test_movie_12345', headers={'Accept': 'text/html'}).headers['content-type'].startswith('text/html')
    assert client.head('/').status_code == 200
    for path in ('/api/missing', '/auth/missing'):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers['content-type'] == 'application/json'
    assert client.get('/health').json() == {'status': 'ok'}


def test_default_shared_folder(client, monkeypatch):
    monkeypatch.setattr(main, 'DEFAULT_DRIVE_FOLDER_URL', 'https://drive.google.com/drive/folders/test_folder_12345')
    response = client.get('/api/me')
    assert response.json()['connected'] is False
    assert main.folder_id_from_input(response.json()['default_folder_url']) == 'test_folder_12345'


def test_production_rejects_local_database(client, monkeypatch):
    monkeypatch.setattr(main, 'APP_ENV', 'production')
    with pytest.raises(RuntimeError, match='TURSO_DATABASE_URL'):
        with TestClient(main.app):
            pass


def test_database_startup_retries_transient_dns_errors(client, monkeypatch):
    import asyncio

    attempts = []
    delays = []

    def initialize():
        attempts.append(True)
        if len(attempts) < 3:
            raise ValueError('Hrana: dns error: Temporary failure in name resolution')

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(main, 'init_db', initialize)
    monkeypatch.setattr(main.asyncio, 'sleep', sleep)
    asyncio.run(main.initialize_database())
    assert len(attempts) == 3
    assert delays == [1, 2]


def test_database_startup_does_not_hide_permanent_errors(client, monkeypatch):
    import asyncio

    def initialize():
        raise ValueError('Invalid database credentials')

    monkeypatch.setattr(main, 'init_db', initialize)
    with pytest.raises(ValueError, match='Invalid database credentials'):
        asyncio.run(main.initialize_database())


def test_enrichment_returns_metadata_for_cache(client, monkeypatch):
    import asyncio

    import httpx

    title = secrets.token_urlsafe(20)

    def respond(request):
        return httpx.Response(200, json={'results': [{'overview': 'Una película', 'release_date': '2023-01-01'}]})

    async def fetch_metadata():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            movie = {'title': title, 'year': '2023'}
            metadata = await main.enrich(movie, http_client, asyncio.Semaphore(1))
            assert movie['overview'] == 'Una película'
            assert metadata['overview'] == movie['overview']
            assert metadata['year'] == '2023'

    asyncio.run(fetch_metadata())


@pytest.mark.parametrize('granted', [False, True])
def test_callback_requires_drive_permission(client, monkeypatch, granted):
    import httpx
    from urllib.parse import parse_qs, urlparse

    user_id = secrets.token_urlsafe(20)
    requests = []

    def respond(request):
        requests.append(str(request.url))
        if str(request.url) == main.TOKEN_URL:
            return httpx.Response(200, json={
                'access_token': 'oauth-access-token', 'refresh_token': 'oauth-refresh-token',
                'scope': main.SCOPES if granted else 'openid email profile',
            })
        return httpx.Response(200, json={'sub': user_id, 'name': 'Test', 'email': 'test@example.com'})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    login = client.get('/auth/login', follow_redirects=False)
    params = parse_qs(urlparse(login.headers['location']).query)
    assert params['include_granted_scopes'] == ['true']
    response = client.get('/auth/callback', params={'state': params['state'][0], 'code': 'oauth-code'},
                          follow_redirects=False)
    assert response.status_code == 307
    assert 'cm_oauth' not in client.cookies
    assert client.get('/api/me').json()['connected'] is granted
    with main.db() as connection:
        sessions = connection.execute(text('SELECT COUNT(*) FROM sessions')).scalar_one()
    assert sessions == int(granted)
    if not granted:
        assert 'error=drive_scope_required' in response.headers['location']
        assert requests == [main.TOKEN_URL]


@pytest.mark.parametrize('reason', ['insufficientPermissions', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'forbidden'])
def test_drive_permission_error(client, monkeypatch, reason):
    import asyncio

    import httpx

    def respond(request):
        return httpx.Response(403, json={'error': {'errors': [{'reason': reason}]}})

    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    with pytest.raises(main.HTTPException) as failure:
        asyncio.run(main.google_get(main.DRIVE_URL, 'access-token'))
    assert failure.value.status_code == 403
    if reason == 'forbidden':
        assert isinstance(failure.value.detail, str)
    else:
        assert failure.value.detail['code'] == 'drive_scope_required'


def test_sync_pagination_resume_categories_and_cleanup(client, monkeypatch):
    user_id = connect_user(client)
    monkeypatch.setattr(main, 'SYNC_FOLDER_BATCH', 1)
    calls = []

    async def google_get(url, token, params=None):
        if url != main.DRIVE_URL:
            return {'mimeType': main.FOLDER_MIME, 'name': 'Biblioteca'}
        calls.append(params)
        if 'child_folder_12345' in params['q']:
            return {'files': [{'id': 'movie_child_12345', 'name': 'Third.mp4'}]}
        if params.get('pageToken'):
            return {'files': [{'id': 'movie_second_12345', 'name': 'Second.mp4'}]}
        return {'nextPageToken': 'page-2', 'files': [
            {'id': 'movie_first_12345', 'name': 'First.mp4'},
            {'id': 'child_folder_12345', 'name': 'Documentales', 'mimeType': main.FOLDER_MIME},
        ]}

    monkeypatch.setattr(main, 'google_get', google_get)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    with main.db() as connection:
        main.save_movies(connection, user_id, [{'id': 'stale_movie_12345', 'title': 'Old'}])
    first = client.post('/api/sync').json()
    assert first['status'] == 'running'
    assert first['count'] == 1
    assert len(first['movies']) == 1
    assert client.get('/api/movies').json()['count'] == 2
    client.patch('/api/movies/movie_first_12345', json={'favorite': True})
    client.post('/api/library', json={'url': 'test_folder_12345'})
    assert client.get('/api/movies').json()['sync']['count'] == 1
    with main.db() as connection:
        connection.execute(text('UPDATE sync_jobs SET locked_until=:until WHERE user_id=:user_id'),
                           {'until': int(time.time()) + 60, 'user_id': user_id})
    assert client.post('/api/sync').json()['movies'] == []
    assert len(calls) == 1
    with main.db() as connection:
        connection.execute(text('UPDATE sync_jobs SET locked_until=0 WHERE user_id=:user_id'), {'user_id': user_id})
    assert client.post('/api/sync').json()['status'] == 'running'
    assert calls[-1]['pageToken'] == 'page-2'
    completed = client.post('/api/sync').json()
    assert completed['status'] == 'completed'
    assert completed['count'] == 3
    movies = {movie['id']: movie for movie in client.get('/api/movies').json()['movies']}
    assert 'stale_movie_12345' not in movies
    assert movies['movie_child_12345']['category'] == 'Documentales'
    assert movies['movie_first_12345']['favorite'] is True
    assert client.post('/api/sync').json()['status'] == 'completed'
    assert len(calls) == 3


def test_sync_failure_keeps_progress_and_releases_lease(client, monkeypatch):
    connect_user(client)
    attempts = 0

    async def google_get(url, token, params=None):
        nonlocal attempts
        if url != main.DRIVE_URL:
            return {'mimeType': main.FOLDER_MIME, 'name': 'Biblioteca'}
        attempts += 1
        if attempts == 2:
            raise main.HTTPException(502, 'Drive temporalmente no disponible')
        if params.get('pageToken'):
            return {'files': []}
        return {'nextPageToken': 'next', 'files': [{'id': 'movie_first_12345', 'name': 'First.mp4'}]}

    monkeypatch.setattr(main, 'google_get', google_get)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    client.post('/api/sync')
    assert client.post('/api/sync').status_code == 502
    catalog = client.get('/api/movies').json()
    assert catalog['sync']['count'] == 1
    assert catalog['count'] == 1
    assert client.post('/api/sync').json()['status'] == 'completed'


def test_limited_sync_does_not_delete_unseen_movies(client, drive, monkeypatch):
    user_id = connect_user(client)
    monkeypatch.setattr(main, 'SYNC_ITEM_LIMIT', 1)
    original_google_get = main.google_get

    async def paginated(url, token, params=None):
        payload = await original_google_get(url, token, params)
        if url == main.DRIVE_URL:
            payload['nextPageToken'] = 'more'
        return payload

    monkeypatch.setattr(main, 'google_get', paginated)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    with main.db() as connection:
        main.save_movies(connection, user_id, [{'id': 'stale_movie_12345', 'title': 'Old'}])
    result = client.post('/api/sync').json()
    assert result['limited'] is True
    assert 'stale_movie_12345' in {movie['id'] for movie in client.get('/api/movies').json()['movies']}


def test_metadata_runs_in_small_batches_and_uses_cache(client, monkeypatch):
    import httpx

    connect_user(client)
    monkeypatch.setattr(main, 'TMDB_TOKEN', 'test-tmdb-token')
    batch_size = main.SYNC_METADATA_BATCH
    total = batch_size + 2
    searches = []

    async def google_get(url, token, params=None):
        if url != main.DRIVE_URL:
            return {'mimeType': main.FOLDER_MIME, 'name': 'Biblioteca'}
        return {'files': [{'id': f'movie_{index}_12345', 'name': f'Movie {index}.mp4'} for index in range(total)]}

    def respond(request):
        searches.append(request.url.params['query'])
        return httpx.Response(200, json={'results': [{
            'poster_path': '/poster.jpg', 'overview': 'Una película', 'genre_ids': [18, 99], 'popularity': 42.75,
        }]})

    monkeypatch.setattr(main, 'google_get', google_get)
    async_client = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, 'AsyncClient',
                        lambda **options: async_client(transport=httpx.MockTransport(respond), **options))
    client.post('/api/library', json={'url': 'test_folder_12345'})
    scanned = client.post('/api/sync').json()
    assert scanned['phase'] == 'metadata'
    assert scanned['count'] == total
    assert not searches
    first = client.post('/api/sync').json()
    assert first['status'] == 'running'
    assert first['enriched'] == batch_size
    assert len(searches) == batch_size
    assert first['movies'][0]['genre'] == 'Drama, Documental'
    assert first['movies'][0]['popularity'] == 42.75
    completed = client.post('/api/sync').json()
    assert completed['status'] == 'completed'
    assert len(searches) == total
    assert all(movie['poster'].endswith('/poster.jpg') for movie in client.get('/api/movies').json()['movies'])
    client.post('/api/sync?start=true')
    client.post('/api/sync')
    client.post('/api/sync')
    assert len(searches) == total
    with main.db() as connection:
        state = json.loads(connection.execute(text('SELECT state FROM sync_jobs')).scalar_one())
        state.update({'metadata_version': main.METADATA_VERSION - 1, 'phase': 'metadata',
                      'status': 'running', 'enriched': total - 1})
        connection.execute(text('UPDATE sync_jobs SET state=:state'), {'state': json.dumps(state)})
    upgraded = client.post('/api/sync').json()
    assert upgraded['enriched'] == batch_size
    assert upgraded['status'] == 'running'
    assert all(movie['popularity'] == 42.75 for movie in upgraded['movies'])
    assert len(searches) == total


def test_sync_reads_folders_concurrently_in_bounded_batches(client, monkeypatch):
    import asyncio

    connect_user(client)
    batch_size = main.SYNC_FOLDER_BATCH
    total = batch_size + 2
    requested = []
    active = 0
    peak = 0

    async def google_get(url, token, params=None):
        nonlocal active, peak
        if url != main.DRIVE_URL:
            return {'mimeType': main.FOLDER_MIME, 'name': 'Biblioteca'}
        if 'test_folder_12345' in params['q']:
            return {'files': [{'id': f'child_{index}_12345', 'name': f'Category {index}',
                               'mimeType': main.FOLDER_MIME} for index in range(total)]}
        requested.append(params['q'])
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        folder_id = params['q'].split("'")[1]
        return {'files': [{'id': f'movie_{folder_id}', 'name': f'{folder_id}.mp4'}]}

    monkeypatch.setattr(main, 'google_get', google_get)
    client.post('/api/library', json={'url': 'test_folder_12345'})
    assert client.post('/api/sync').json()['status'] == 'running'
    first = client.post('/api/sync').json()
    assert first['count'] == batch_size
    assert first['status'] == 'running'
    assert len(requested) == batch_size
    assert peak == batch_size
    last = client.post('/api/sync').json()
    assert last['status'] == 'completed'
    assert last['count'] == total
