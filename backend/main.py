"""Cinemateca: a private Google Drive film library. Python 3.13+.

OAuth tokens NEVER leave this server; encryption key must be kept outside git.
Video playback is limited to files indexed from the selected folder.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import time
import unicodedata
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import DBAPIError

from backend.media import DriveSource, compatible_command, subtitle_to_vtt

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
DB_PATH = Path(os.getenv('SQLITE_PATH', str(BASE / 'data' / 'cinemateca.sqlite3')))
TURSO_DATABASE_URL = os.getenv('TURSO_DATABASE_URL', '')
TURSO_AUTH_TOKEN = os.getenv('TURSO_AUTH_TOKEN', '')
if TURSO_DATABASE_URL:
    turso_url = urlparse(TURSO_DATABASE_URL)
    if turso_url.scheme not in {'libsql', 'https'} or not turso_url.hostname or not TURSO_AUTH_TOKEN:
        raise RuntimeError('Configure a libsql:// or https:// TURSO_DATABASE_URL and TURSO_AUTH_TOKEN')
    engine = create_engine(
        URL.create('sqlite+libsql', host=turso_url.hostname, port=turso_url.port, query={'secure': 'true'}),
        connect_args={'auth_token': TURSO_AUTH_TOKEN}, pool_pre_ping=True,
    )
else:
    engine = create_engine(f'sqlite:///{DB_PATH}', pool_pre_ping=True)
APP_ENV = os.getenv('APP_ENV', 'development')
CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:8000/auth/callback')
WEB_REDIRECT = os.getenv('WEB_REDIRECT', '/')
COOKIE_SECURE = os.getenv('COOKIE_SECURE', str(APP_ENV == 'production')).lower() == 'true'
DEFAULT_DRIVE_FOLDER_URL = os.getenv('DEFAULT_DRIVE_FOLDER_URL', '')
TMDB_TOKEN = os.getenv('TMDB_API_TOKEN', '')
FERNET_KEY = os.getenv('TOKEN_ENCRYPTION_KEY', '')
TOKEN_URL = 'https://oauth2.googleapis.com/token'
DRIVE_URL = 'https://www.googleapis.com/drive/v3/files'
FOLDER_MIME = 'application/vnd.google-apps.folder'
VIDEO_EXT = {'.mp4', '.webm', '.m4v', '.mov', '.mkv', '.avi'}
PLAYABLE_EXT = {'.mp4', '.webm', '.m4v'}
ID_RE = re.compile(r'^[a-zA-Z0-9_-]{10,150}$')
RANGE_RE = re.compile(r'^bytes=(?:\d+-\d*|-\d+)$')
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
SCOPES = f'openid email profile {DRIVE_SCOPE}'
SYNC_PAGE_SIZE = 100
SYNC_FOLDER_BATCH = 10
SYNC_METADATA_BATCH = 10
SYNC_ITEM_LIMIT = 2000
METADATA_VERSION = 4
TMDB_CACHE_TTL = 24 * 60 * 60
CAST_TICKET_TTL = 12 * 60 * 60
TRANSCODE_SLOTS = asyncio.Semaphore(2)
SUBTITLE_EXT = {'.srt', '.vtt', '.ass', '.ssa'}
SUBTITLE_LIMIT = 2 * 1024 * 1024
TMDB_GENRES = {
    28: 'Acción', 12: 'Aventura', 16: 'Animación', 35: 'Comedia', 80: 'Crimen',
    99: 'Documental', 18: 'Drama', 10751: 'Familia', 14: 'Fantasía', 36: 'Historia',
    27: 'Terror', 10402: 'Música', 9648: 'Misterio', 10749: 'Romance',
    878: 'Ciencia ficción', 10770: 'Película de TV', 53: 'Suspenso', 10752: 'Bélica', 37: 'Western',
}


def db():
    if engine.dialect.driver == 'pysqlite':
        database = engine.url.database
        if database and database != ':memory:':
            Path(database).parent.mkdir(parents=True, exist_ok=True)
    return engine.begin()


def init_db():
    with db() as con:
        schema = '''
            CREATE TABLE IF NOT EXISTS sessions(
                sid TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT,
                email TEXT, access_cipher TEXT NOT NULL, refresh_cipher TEXT,
                expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS libraries(
                user_id TEXT PRIMARY KEY, folder_id TEXT NOT NULL,
                folder_name TEXT NOT NULL, synced_at INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS movies(
                user_id TEXT NOT NULL, file_id TEXT NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(user_id, file_id)
            );
            CREATE TABLE IF NOT EXISTS edits(
                user_id TEXT NOT NULL, file_id TEXT NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(user_id, file_id)
            );
            CREATE TABLE IF NOT EXISTS tmdb_cache(
                title_key TEXT PRIMARY KEY, data TEXT NOT NULL, checked_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sync_jobs(
                user_id TEXT PRIMARY KEY, folder_id TEXT NOT NULL, state TEXT NOT NULL,
                lease_id TEXT NOT NULL DEFAULT '', locked_until INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS public_library(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), user_id TEXT NOT NULL,
                folder_id TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
                access_cipher TEXT NOT NULL, refresh_cipher TEXT, expires_at INTEGER NOT NULL
            );
        '''
        for statement in schema.split(';'):
            if statement.strip():
                con.execute(text(statement))


async def initialize_database():
    for attempt in range(5):
        try:
            await asyncio.to_thread(init_db)
            return
        except (ValueError, DBAPIError) as exc:
            transient = any(message in str(exc).casefold() for message in (
                'dns error', 'temporary failure', 'timed out', 'timeout',
                'connection refused', 'connection reset', 'connect error'))
            if not transient or attempt == 4:
                raise
            engine.dispose()
            logging.getLogger(__name__).warning('Base de datos no disponible al iniciar; reintentando (%s/5).', attempt + 2)
            await asyncio.sleep(2 ** attempt)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if APP_ENV == 'production':
        if not TURSO_DATABASE_URL:
            raise RuntimeError('Production requires TURSO_DATABASE_URL and TURSO_AUTH_TOKEN')
        if not CLIENT_ID or not CLIENT_SECRET or not FERNET_KEY:
            raise RuntimeError('Production requires Google OAuth credentials and TOKEN_ENCRYPTION_KEY')
        if not COOKIE_SECURE or not REDIRECT_URI.startswith('https://'):
            raise RuntimeError('Production requires COOKIE_SECURE=true and an HTTPS GOOGLE_REDIRECT_URI')
        if not (DIST / 'index.html').is_file():
            raise RuntimeError('Build the frontend before deploying: npm --prefix frontend run build')
    if CLIENT_ID and CLIENT_SECRET:
        cipher()
    await initialize_database()
    worker = asyncio.create_task(resume_sync_jobs())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        engine.dispose()


app = FastAPI(title='Cinemateca API', version='0.3.0', lifespan=lifespan)
DIST = BASE.parent / 'frontend' / 'dist'


@app.middleware('http')
async def media_cors(request: Request, call_next):
    media = bool(re.fullmatch(r'/api/(?:stream/[^/]+|play/[^/]+|movies/[^/]+/subtitles/[^/]+)', request.url.path))
    if media and request.method == 'OPTIONS':
        response = Response(status_code=204)
    else:
        response = await call_next(request)
    if media:
        response.headers.update({
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
            'Access-Control-Allow-Headers': 'Range, Content-Type',
            'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges',
            'Referrer-Policy': 'no-referrer',
        })
    return response


def cipher():
    if not FERNET_KEY:
        raise HTTPException(503, 'TOKEN_ENCRYPTION_KEY no configurada')
    try:
        return Fernet(FERNET_KEY.encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, 'TOKEN_ENCRYPTION_KEY inválida') from exc


def encrypt(text: str | None) -> str | None:
    return cipher().encrypt(text.encode()).decode() if text else None


def decrypt(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return cipher().decrypt(text.encode()).decode()
    except InvalidToken as exc:
        raise HTTPException(401, 'No se pudieron recuperar las credenciales') from exc


def validate_id(value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise HTTPException(400, 'Identificador de Drive inválido')
    return value


def folder_id_from_input(value: str) -> str:
    value = value.strip()
    if ID_RE.fullmatch(value):
        return value
    url = urlparse(value)
    if url.scheme != 'https' or url.netloc not in {'drive.google.com', 'www.drive.google.com'}:
        raise HTTPException(400, 'Pegá el enlace de una carpeta de Google Drive')
    match = re.search(r'/folders/([a-zA-Z0-9_-]{10,150})(?:/|$)', url.path)
    if not match:
        raise HTTPException(400, 'No se encontró el ID de la carpeta')
    return validate_id(match.group(1))


def filename_title(name: str) -> tuple[str, str]:
    title = re.sub(r'\.(mp4|webm|m4v|mkv|avi|mov)$', '', name, flags=re.I)
    title = re.sub(r'^\d{1,3}[.)]\s+', '', title)
    match = re.search(r'(?<!\d)(19\d{2}|20\d{2})(?!\d)', title)
    year = match.group(1) if match else ''
    title = re.split(r'(?i)\b(?:19\d{2}|20\d{2}|1080p|2160p|720p|4k|bluray|webrip|web-dl|h264|x264|h265|x265|aac|hevc)\b', title)[0]
    return re.sub(r'\s+', ' ', re.sub(r'[._\[\](){}-]+', ' ', title)).strip() or name, year


def normalized(value: str) -> str:
    return ''.join(character for character in unicodedata.normalize('NFD', value)
                   if not unicodedata.combining(character)).casefold()


def is_video(item: dict) -> bool:
    return item.get('mimeType', '').startswith('video/') or Path(item.get('name', '')).suffix.lower() in VIDEO_EXT


def is_csrf_safe(request: Request):
    # SameSite=Lax cookies are the first line of defense; additionally verify Origin.
    origin = request.headers.get('origin')
    if origin:
        host = request.headers.get('x-forwarded-host', request.url.netloc)
        expected = {f'https://{host}', f'http://{host}'}
        parsed = urlparse(WEB_REDIRECT)
        if parsed.scheme and parsed.netloc:
            expected.add(f'{parsed.scheme}://{parsed.netloc}')
        if origin not in expected:
            raise HTTPException(403, 'Origen no permitido')


async def google_get(url: str, token: str, params: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(url, headers={'Authorization': f'Bearer {token}'}, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(502, 'No se pudo contactar Google Drive') from exc
    if resp.status_code >= 400:
        if resp.status_code == 403:
            try:
                error = resp.json().get('error', {})
                reasons = {entry.get('reason') for entry in error.get('errors', []) + error.get('details', [])}
            except (ValueError, AttributeError, TypeError):
                reasons = set()
            if reasons & {'insufficientPermissions', 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'}:
                raise HTTPException(403, {
                    'code': 'drive_scope_required',
                    'message': 'Falta autorizar el acceso a Drive. Volvé a conectar y marcá el permiso para ver y descargar archivos.',
                })
        raise HTTPException(resp.status_code if resp.status_code in (400, 401, 403, 404, 429) else 502,
                            'Drive devolvió un error de permisos o disponibilidad')
    return resp.json()


async def require_user(request: Request) -> dict:
    sid = request.cookies.get('cm_sid', '')
    if not sid:
        raise HTTPException(401, 'Conectá tu cuenta de Google')
    with db() as con:
        row = con.execute(text('SELECT * FROM sessions WHERE sid=:sid'), {'sid': sid}).mappings().first()
    if not row:
        raise HTTPException(401, 'Sesión no encontrada')
    return await authorize_credentials(dict(row))


async def authorize_credentials(user: dict, shared: bool = False) -> dict:
    user['access_token'] = decrypt(user['access_cipher'])
    if user['expires_at'] < time.time() + 90:
        token = decrypt(user['refresh_cipher'])
        if not token:
            raise HTTPException(401, 'La sesión venció: volvé a conectar Drive')
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                result = await client.post(TOKEN_URL, data={
                    'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
                    'refresh_token': token, 'grant_type': 'refresh_token'
                })
        except httpx.RequestError as exc:
            raise HTTPException(502, 'No se pudo renovar el acceso a Drive') from exc
        if result.status_code != 200:
            raise HTTPException(401, 'El acceso a Drive venció o fue revocado')
        payload = result.json()
        user['access_token'] = payload['access_token']
        user['expires_at'] = int(time.time()) + int(payload.get('expires_in', 3600))
        with db() as con:
            if not shared:
                con.execute(text('UPDATE sessions SET access_cipher=:access_cipher,expires_at=:expires_at WHERE sid=:sid'),
                            {'access_cipher': encrypt(user['access_token']), 'expires_at': user['expires_at'], 'sid': user['sid']})
            con.execute(text('UPDATE public_library SET access_cipher=:access_cipher,expires_at=:expires_at WHERE user_id=:user_id'),
                        {'access_cipher': encrypt(user['access_token']), 'expires_at': user['expires_at'], 'user_id': user['user_id']})
    return user


def public_library():
    with db() as con:
        row = con.execute(text('SELECT * FROM public_library WHERE singleton=1')).mappings().first()
    return dict(row) if row else None


async def require_manager(request: Request) -> dict:
    user = await require_user(request)
    shared = public_library()
    if shared and shared['user_id'] != user['user_id']:
        raise HTTPException(403, 'Solo el propietario puede administrar esta cinemateca')
    return user


async def require_reader(request: Request, credentials: bool = False) -> dict:
    shared = public_library()
    if shared and shared['enabled']:
        return await authorize_credentials(shared, shared=True) if credentials else shared
    return await require_user(request)


async def playback_reader(request: Request, file_id: str, cast_token: str) -> dict:
    if not cast_token:
        return await require_reader(request, credentials=True)
    try:
        ticket = json.loads(cipher().decrypt(cast_token.encode(), ttl=CAST_TICKET_TTL))
        if ticket['purpose'] != 'cast' or ticket['file_id'] != file_id:
            raise ValueError()
        library = require_library(ticket['user_id'])
        if library['folder_id'] != ticket['folder_id']:
            raise ValueError()
        if ticket.get('sid'):
            with db() as con:
                row = con.execute(text('SELECT * FROM sessions WHERE sid=:sid AND user_id=:user_id'),
                                  {'sid': ticket['sid'], 'user_id': ticket['user_id']}).mappings().first()
            shared = False
        else:
            row = public_library()
            shared = True
            if not row or not row['enabled'] or row['user_id'] != ticket['user_id']:
                raise ValueError()
        if not row:
            raise ValueError()
    except (InvalidToken, ValueError, KeyError, TypeError, HTTPException) as exc:
        raise HTTPException(401, 'El enlace de Chromecast venció o fue revocado. Volvé a transmitir la película.') from exc
    return await authorize_credentials(dict(row), shared=shared)


@app.post('/api/movies/{file_id}/cast')
async def cast_ticket(request: Request, file_id: str):
    is_csrf_safe(request)
    user = await require_reader(request)
    library_movie(user['user_id'], file_id)
    library = require_library(user['user_id'])
    ticket = {'purpose': 'cast', 'file_id': file_id, 'user_id': user['user_id'],
              'folder_id': library['folder_id'], 'sid': user.get('sid')}
    token = cipher().encrypt(json.dumps(ticket).encode()).decode()
    return JSONResponse({'token': token, 'expires_at': int(time.time()) + CAST_TICKET_TTL},
                        headers={'Cache-Control': 'no-store'})


@app.get('/auth/login')
async def login():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(503, 'Google OAuth todavía no está configurado')
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI,
              'response_type': 'code', 'scope': SCOPES, 'access_type': 'offline',
              'prompt': 'consent', 'state': state, 'include_granted_scopes': 'true',
              'code_challenge': challenge, 'code_challenge_method': 'S256'}
    response = RedirectResponse('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params))
    response.set_cookie('cm_oauth', json.dumps({'state': state, 'verifier': verifier}),
                        max_age=600, httponly=True, secure=COOKIE_SECURE, samesite='lax')
    return response


@app.get('/auth/callback')
async def callback(request: Request, state: str = '', code: str = '', error: str = ''):
    if error:
        return RedirectResponse(WEB_REDIRECT + ('&' if '?' in WEB_REDIRECT else '?') + 'error=oauth_denied')
    try:
        data = json.loads(request.cookies.get('cm_oauth', '{}'))
    except (ValueError, TypeError):
        data = {}
    if not state or not code or not secrets.compare_digest(state, data.get('state', '')):
        raise HTTPException(400, 'Estado OAuth inválido')
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(TOKEN_URL, data={
            'code': code, 'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'redirect_uri': REDIRECT_URI, 'grant_type': 'authorization_code',
            'code_verifier': data['verifier']
        })
        if response.status_code != 200:
            raise HTTPException(502, 'Google rechazó el intercambio OAuth')
        token = response.json()
        if DRIVE_SCOPE not in token.get('scope', '').split():
            out = RedirectResponse(WEB_REDIRECT + ('&' if '?' in WEB_REDIRECT else '?') + 'error=drive_scope_required')
            out.delete_cookie('cm_oauth')
            return out
        profile = await client.get('https://www.googleapis.com/oauth2/v3/userinfo', headers={
            'Authorization': f"Bearer {token['access_token']}"})
        if profile.status_code != 200:
            raise HTTPException(502, 'No se pudo obtener el perfil de Google')
    info = profile.json()
    now = int(time.time())
    sid = secrets.token_urlsafe(40)
    with db() as con:
        con.execute(text('''INSERT INTO sessions
            (sid,user_id,name,email,access_cipher,refresh_cipher,expires_at,created_at)
            VALUES(:sid,:user_id,:name,:email,:access_cipher,:refresh_cipher,:expires_at,:created_at)'''), {
            'sid': sid, 'user_id': info['sub'], 'name': info.get('name', ''), 'email': info.get('email', ''),
            'access_cipher': encrypt(token['access_token']), 'refresh_cipher': encrypt(token.get('refresh_token')),
            'expires_at': now + int(token.get('expires_in', 3600)), 'created_at': now})
        con.execute(text('''UPDATE public_library SET access_cipher=:access_cipher,
                    refresh_cipher=COALESCE(:refresh_cipher,refresh_cipher),expires_at=:expires_at
                    WHERE user_id=:user_id'''), {
            'access_cipher': encrypt(token['access_token']), 'refresh_cipher': encrypt(token.get('refresh_token')),
            'expires_at': now + int(token.get('expires_in', 3600)), 'user_id': info['sub']})
    out = RedirectResponse(WEB_REDIRECT)
    out.delete_cookie('cm_oauth')
    out.set_cookie('cm_sid', sid, httponly=True, secure=COOKIE_SECURE,
                   samesite='lax', max_age=60*60*24*14)
    return out


@app.post('/auth/logout')
async def logout(request: Request):
    is_csrf_safe(request)
    sid = request.cookies.get('cm_sid','')
    with db() as con:
        con.execute(text('DELETE FROM sessions WHERE sid=:sid'), {'sid': sid})
    response = JSONResponse({'ok': True})
    response.delete_cookie('cm_sid')
    return response


@app.get('/api/me')
async def me(request: Request):
    shared = public_library()
    try:
        user = await require_user(request)
    except HTTPException:
        user = None
    manager = bool(user and (not shared or shared['user_id'] == user['user_id']))
    if not manager:
        return {'connected': False, 'configured': bool(CLIENT_ID and CLIENT_SECRET),
                'public': bool(shared and shared['enabled']), 'can_manage': False,
                **({'default_folder_url': DEFAULT_DRIVE_FOLDER_URL} if not shared else {})}
    with db() as con:
        row = con.execute(text('SELECT * FROM libraries WHERE user_id=:user_id'),
                          {'user_id': user['user_id']}).mappings().first()
    return {'connected': True, 'configured': True, 'name': user['name'],
            'email': user['email'], 'library': dict(row) if row else None,
            'can_manage': True, 'public': bool(shared and shared['enabled']),
            'default_folder_url': DEFAULT_DRIVE_FOLDER_URL}


class FolderInput(BaseModel):
    url: str = Field(min_length=10, max_length=1000)


class SharingInput(BaseModel):
    enabled: bool


@app.post('/api/sharing')
async def set_sharing(request: Request, payload: SharingInput):
    is_csrf_safe(request)
    user = await require_manager(request)
    library = require_library(user['user_id'])
    if payload.enabled and not user.get('refresh_cipher'):
        raise HTTPException(400, 'Volvé a autorizar Drive para guardar el acceso permanente del servidor')
    with db() as con:
        saved = con.execute(text('''INSERT INTO public_library
                    (singleton,user_id,folder_id,enabled,access_cipher,refresh_cipher,expires_at)
                    VALUES(1,:user_id,:folder_id,:enabled,:access_cipher,:refresh_cipher,:expires_at)
                    ON CONFLICT(singleton) DO UPDATE SET folder_id=excluded.folder_id,enabled=excluded.enabled,
                    access_cipher=excluded.access_cipher,refresh_cipher=excluded.refresh_cipher,expires_at=excluded.expires_at
                    WHERE public_library.user_id=excluded.user_id RETURNING user_id'''), {
            'user_id': user['user_id'], 'folder_id': library['folder_id'], 'enabled': int(payload.enabled),
            'access_cipher': encrypt(user['access_token']), 'refresh_cipher': user['refresh_cipher'],
            'expires_at': user['expires_at']}).scalar_one_or_none()
        if saved is None:
            raise HTTPException(403, 'Solo el propietario puede compartir esta cinemateca')
    return {'public': payload.enabled}


@app.post('/api/library')
async def set_library(request: Request, payload: FolderInput):
    is_csrf_safe(request)
    user = await require_manager(request)
    folder_id = folder_id_from_input(payload.url)
    folder = await google_get(f'{DRIVE_URL}/{folder_id}', user['access_token'],
                              {'fields': 'id,name,mimeType', 'supportsAllDrives': 'true'})
    if folder.get('mimeType') != FOLDER_MIME:
        raise HTTPException(400, 'No es una carpeta de Drive')
    with db() as con:
        previous = con.execute(text('SELECT folder_id FROM libraries WHERE user_id=:user_id'),
                               {'user_id': user['user_id']}).scalar_one_or_none()
        con.execute(text('''INSERT INTO libraries(user_id,folder_id,folder_name,synced_at)
                       VALUES(:user_id,:folder_id,:folder_name,0) ON CONFLICT(user_id) DO UPDATE SET
                       folder_name=excluded.folder_name,
                       synced_at=CASE WHEN libraries.folder_id=excluded.folder_id THEN libraries.synced_at ELSE 0 END,
                       folder_id=excluded.folder_id'''),
                    {'user_id': user['user_id'], 'folder_id': folder_id, 'folder_name': folder['name']})
        if previous != folder_id:
            con.execute(text('UPDATE public_library SET enabled=0 WHERE user_id=:user_id'), {'user_id': user['user_id']})
            con.execute(text('DELETE FROM movies WHERE user_id=:user_id'), {'user_id': user['user_id']})
            con.execute(text('DELETE FROM sync_jobs WHERE user_id=:user_id'), {'user_id': user['user_id']})
    return {'folder_id': folder_id, 'folder_name': folder['name']}


def require_library(user_id: str):
    with db() as con:
        row = con.execute(text('SELECT * FROM libraries WHERE user_id=:user_id'),
                          {'user_id': user_id}).mappings().first()
    if not row:
        raise HTTPException(404, 'Elegí primero una carpeta de Google Drive')
    return dict(row)


def sync_summary(state: dict, folder_name: str) -> dict:
    return {'status': state['status'], 'phase': state['phase'], 'count': len(state['ids']),
            'processed': state['processed'], 'enriched': state['enriched'],
            'limited': state['limited'], 'folder': folder_name}


def library_sync(user_id: str, folder_name: str) -> dict | None:
    with db() as con:
        state = con.execute(text('SELECT state FROM sync_jobs WHERE user_id=:user_id'),
                            {'user_id': user_id}).scalar_one_or_none()
    return sync_summary(json.loads(state), folder_name) if state else None


def save_movies(con, user_id: str, items: list[dict]):
    if not items:
        return
    values = []
    params = {'user_id': user_id}
    for index, movie in enumerate(items):
        values.append(f'(:user_id,:file_{index},:metadata_{index})')
        params[f'file_{index}'] = movie['id']
        params[f'metadata_{index}'] = json.dumps(movie)
    con.execute(text('INSERT INTO movies(user_id,file_id,metadata) VALUES ' + ','.join(values) +
                     ' ON CONFLICT(user_id,file_id) DO UPDATE SET metadata=excluded.metadata'), params)


async def scan_page(token: str, state: dict) -> list[dict]:
    folders = state['pending'][:SYNC_FOLDER_BATCH]

    async def fetch(folder):
        params = {'q': f"'{folder['id']}' in parents and trashed = false",
                  'fields': 'nextPageToken,files(id,name,mimeType,size,modifiedTime,videoMediaMetadata,capabilities/canDownload)',
                  'pageSize': min(SYNC_PAGE_SIZE, SYNC_ITEM_LIMIT - state['processed']),
                  'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        if folder.get('page_token'):
            params['pageToken'] = folder['page_token']
        return await google_get(DRIVE_URL, token, params)

    results = await asyncio.gather(*(fetch(folder) for folder in folders))
    items = []
    for folder, result in zip(folders, results):
        for item in result.get('files', []):
            if state['processed'] >= SYNC_ITEM_LIMIT:
                state['limited'] = True
                break
            state['processed'] += 1
            if item.get('mimeType') == FOLDER_MIME:
                if folder['depth'] < 5 and item['id'] not in state['folders']:
                    state['folders'].append(item['id'])
                    state['pending'].append({'id': item['id'], 'depth': folder['depth'] + 1,
                                             'category': '/'.join(filter(None, [folder['category'], item['name']]))})
                elif folder['depth'] >= 5:
                    state['limited'] = True
            elif is_video(item) and item['id'] not in state['ids']:
                state['ids'].append(item['id'])
                title, year = filename_title(item['name'])
                items.append({
                    'id': item['id'], 'title': title, 'year': year,
                    'filename': item['name'], 'mime': item.get('mimeType', ''),
                    'parentId': folder['id'],
                    'playable': Path(item['name']).suffix.lower() in PLAYABLE_EXT,
                    'canDownload': item.get('capabilities', {}).get('canDownload', True),
                    'size': int(item.get('size', 0)),
                    'duration': round(int(item.get('videoMediaMetadata', {}).get('durationMillis', 0)) / 60000),
                    'durationSeconds': int(item.get('videoMediaMetadata', {}).get('durationMillis', 0)) / 1000,
                    'addedAt': item.get('modifiedTime', ''), 'category': folder['category'],
                    'genre': '', 'director': '', 'overview': '', 'poster': '', 'backdrop': '',
                    'collection': '', 'favorite': False, 'featured': False,
                })
        if result.get('nextPageToken'):
            folder['page_token'] = result['nextPageToken']
        else:
            state['pending'].remove(folder)
        if state['processed'] >= SYNC_ITEM_LIMIT and state['pending']:
            state['limited'] = True
            state['pending'] = []
            break
    if not state['pending']:
        state['phase'] = 'metadata' if TMDB_TOKEN and state['ids'] else 'completed'
    return items


async def enrich(movie: dict, client: httpx.AsyncClient, semaphore: asyncio.Semaphore):
    async with semaphore:
        try:
            args = {'query': movie['title'], 'language': 'es-AR', 'include_adult':'false'}
            if movie['year']:
                args['year'] = movie['year']
            result = await client.get('https://api.themoviedb.org/3/search/movie', params=args,
                                      headers={'Authorization': f'Bearer {TMDB_TOKEN}'})
            result.raise_for_status()
            matches = result.json().get('results', [])
            hit = matches[0] if matches else {}
            details = {}
            if hit.get('id'):
                response = await client.get(f"https://api.themoviedb.org/3/movie/{hit['id']}",
                    params={'language': 'es-AR', 'append_to_response': 'credits'},
                    headers={'Authorization': f'Bearer {TMDB_TOKEN}'})
                response.raise_for_status()
                details = response.json()
            credits = details.get('credits', {})
            entry = {'poster': 'https://image.tmdb.org/t/p/w780' + hit['poster_path'] if hit.get('poster_path') else '',
                     'backdrop': 'https://image.tmdb.org/t/p/original' + hit['backdrop_path'] if hit.get('backdrop_path') else '',
                     'overview': hit.get('overview', ''),
                     'genre': ', '.join(TMDB_GENRES[genre] for genre in hit.get('genre_ids', []) if genre in TMDB_GENRES),
                     'popularity': float(hit['popularity']) if hit.get('popularity') is not None else None,
                     'tmdbId': hit.get('id'),
                     'country': ', '.join(country.get('name', '') for country in details.get('production_countries', [])),
                     'director': ', '.join(person['name'] for person in credits.get('crew', []) if person.get('job') == 'Director'),
                     'cast': [person['name'] for person in credits.get('cast', [])[:6]],
                     'metadataVersion': METADATA_VERSION,
                     'year': movie['year'] or hit.get('release_date','')[:4]}
            movie.update(entry)
            return entry
        except (httpx.HTTPError, KeyError, ValueError):
            return None


def compose_movies(user_id: str, file_ids: list[str] | None = None) -> list[dict]:
    if file_ids == []:
        return []
    params = {'user_id': user_id}
    selected = ''
    if file_ids is not None:
        params.update({f'file_{index}': file_id for index, file_id in enumerate(file_ids)})
        selected = ' AND file_id IN (' + ','.join(f':file_{index}' for index in range(len(file_ids))) + ')'
    with db() as con:
        movies = con.execute(text('SELECT file_id,metadata FROM movies WHERE user_id=:user_id' + selected), params).mappings().all()
        edits = {x['file_id']:json.loads(x['metadata']) for x in
                 con.execute(text('SELECT file_id,metadata FROM edits WHERE user_id=:user_id' + selected), params).mappings()}
    result = []
    for row in movies:
        item = json.loads(row['metadata'])
        prefix = re.match(r'^(\d{1,3})[.)]\s+', item.get('filename', ''))
        if prefix:
            title, _ = filename_title(item['filename'])
            if item['title'] == f'{prefix.group(1)} {title}':
                item['title'] = title
        item.update(edits.get(row['file_id'], {}))
        result.append(item)
    return sorted(result, key=lambda movie:movie['title'].casefold())


def movie_group_key(movie: dict):
    return f"tmdb:{movie['tmdbId']}" if movie.get('tmdbId') else (normalized(movie['title']), movie.get('year', ''))


@app.get('/api/movies/{file_id}')
async def movie_details(request: Request, file_id: str):
    user = await require_reader(request)
    validate_id(file_id)
    movies = compose_movies(user['user_id'])
    selected = next((movie for movie in movies if movie['id'] == file_id), None)
    if selected is None:
        raise HTTPException(404, 'Esta película no está en tu biblioteca')
    return {'movie': selected, 'versions': [movie for movie in movies if movie_group_key(movie) == movie_group_key(selected)]}


@app.get('/api/movies')
async def movies(request: Request, offset: int = Query(0, ge=0), limit: int = Query(16, ge=1, le=2000),
                 q: str = '', genre: str = '', favorite: bool = False,
                 sort: str = Query('title', pattern='^(title|year|popularity)$')):
    user = await require_reader(request)
    library = require_library(user['user_id'])
    result = compose_movies(user['user_id'])
    genres = sorted({name.strip() for movie in result for name in movie.get('genre', '').split(',') if name.strip()})
    groups = {}
    for movie in result:
        key = movie_group_key(movie)
        groups.setdefault(key, []).append(movie)
    query = normalized(q)
    filtered = [group for group in groups.values() if any(
        (not query or query in normalized(' '.join(str(movie.get(field) or '') for field in
            ('title', 'year', 'director', 'country', 'cast', 'genre', 'filename'))))
        and (not genre or genre in [name.strip() for name in movie.get('genre', '').split(',')])
        and (not favorite or movie.get('favorite')) for movie in group)]
    def ordering(group):
        movie = group[0]
        title = normalized(movie['title'])
        if sort == 'year':
            return (-int(movie.get('year') or 0), title, movie['id'])
        if sort == 'popularity':
            return (-(movie.get('popularity') if movie.get('popularity') is not None else -1), title, movie['id'])
        return (title, movie['id'])
    filtered.sort(key=ordering)
    page = filtered[offset:offset + limit]
    next_offset = offset + len(page)
    return {'movies': [movie for group in page for movie in group], 'folder':library['folder_name'],
            'synced_at':library['synced_at'], 'count':len(filtered), 'genres': genres,
            'next_offset': next_offset if next_offset < len(filtered) else None,
            'sync': library_sync(user['user_id'], library['folder_name'])}


@app.post('/api/sync')
async def sync_movies(request: Request, start: bool = False):
    is_csrf_safe(request)
    user = await require_manager(request)
    library = require_library(user['user_id'])
    return await advance_sync(user, library, start)


async def resume_sync_jobs_once():
    with db() as con:
        jobs = con.execute(text('SELECT user_id,state FROM sync_jobs')).mappings().all()
    for job in jobs:
        state = json.loads(job['state'])
        if state['status'] == 'completed' and (not TMDB_TOKEN or state.get('metadata_version') == METADATA_VERSION):
            continue
        try:
            library = require_library(job['user_id'])
            with db() as con:
                shared = con.execute(text('SELECT * FROM public_library WHERE user_id=:user_id'),
                                     {'user_id': job['user_id']}).mappings().first()
                session = con.execute(text('SELECT * FROM sessions WHERE user_id=:user_id ORDER BY created_at DESC LIMIT 1'),
                                      {'user_id': job['user_id']}).mappings().first()
                if state['status'] == 'completed':
                    state.update(status='running', phase='metadata', enriched=0, metadata_version=METADATA_VERSION)
                    con.execute(text('UPDATE sync_jobs SET state=:state WHERE user_id=:user_id AND state=:previous'),
                                {'state': json.dumps(state), 'user_id': job['user_id'], 'previous': job['state']})
            credentials = shared or session
            if credentials:
                user = await authorize_credentials(dict(credentials), shared=bool(shared))
                await advance_sync(user, library)
        except Exception:
            logging.getLogger(__name__).exception('No se pudo continuar la sincronización guardada')


async def resume_sync_jobs():
    while True:
        await asyncio.sleep(5)
        try:
            await resume_sync_jobs_once()
        except Exception:
            logging.getLogger(__name__).exception('No se pudieron consultar las sincronizaciones pendientes')


async def advance_sync(user: dict, library: dict, start: bool = False):
    user_id = user['user_id']
    lease_id = secrets.token_urlsafe(24)
    with db() as con:
        row = con.execute(text('SELECT state FROM sync_jobs WHERE user_id=:user_id'),
                          {'user_id': user_id}).scalar_one_or_none()
        state = json.loads(row) if row else None
        if state is None or (start and state['status'] == 'completed'):
            state = {'status': 'running', 'phase': 'scan', 'processed': 0, 'enriched': 0,
                     'metadata_version': METADATA_VERSION,
                     'ids': [], 'folders': [library['folder_id']], 'limited': False,
                     'pending': [{'id': library['folder_id'], 'depth': 0, 'category': ''}]}
            con.execute(text('''INSERT INTO sync_jobs(user_id,folder_id,state) VALUES(:user_id,:folder_id,:state)
                        ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,folder_id=excluded.folder_id,
                        lease_id='',locked_until=0'''),
                        {'user_id': user_id, 'folder_id': library['folder_id'], 'state': json.dumps(state)})
        if state['status'] == 'completed':
            return {**sync_summary(state, library['folder_name']), 'movies': []}
        claimed = con.execute(text('''UPDATE sync_jobs SET lease_id=:lease_id,locked_until=:until
                              WHERE user_id=:user_id AND locked_until<=:now RETURNING state'''),
                              {'lease_id': lease_id, 'until': int(time.time()) + 60,
                               'user_id': user_id, 'now': int(time.time())}).scalar_one_or_none()
        if claimed is None:
            return {**sync_summary(state, library['folder_name']), 'movies': []}
        state = json.loads(claimed)
    try:
        cache_updates = []
        if state['phase'] == 'scan':
            changed = await scan_page(user['access_token'], state)
            previous = {movie['id']: movie for movie in compose_movies(user_id, [movie['id'] for movie in changed])}
            for movie in changed:
                saved = previous.get(movie['id'], {})
                for key in ('genre', 'director', 'overview', 'poster', 'backdrop', 'popularity', 'tmdbId', 'country', 'cast', 'metadataVersion'):
                    movie[key] = saved.get(key, movie.get(key))
        else:
            if state.get('metadata_version') != METADATA_VERSION:
                state['enriched'] = 0
                state['metadata_version'] = METADATA_VERSION
            selected = state['ids'][state['enriched']:state['enriched'] + SYNC_METADATA_BATCH]
            changed = compose_movies(user_id, selected)
            keys = {movie['id']: (movie['title'].casefold() + '|' + movie['year'])[:250] for movie in changed}
            params = {f'key_{index}': key for index, key in enumerate(keys.values())}
            with db() as con:
                cached = {row['title_key']: row for row in con.execute(text(
                    'SELECT title_key,data,checked_at FROM tmdb_cache WHERE title_key IN (' +
                    ','.join(f':{key}' for key in params) + ')'), params).mappings()} if params else {}

            async def enrich_one(movie, client, semaphore):
                title_key = keys[movie['id']]
                entry = cached.get(title_key)
                if entry and time.time() - entry['checked_at'] < TMDB_CACHE_TTL:
                    data = json.loads(entry['data'])
                    if data.get('metadataVersion') == METADATA_VERSION:
                        movie.update(data)
                        return
                data = await enrich(movie, client, semaphore)
                if data is not None:
                    cache_updates.append((title_key, data))

            async with httpx.AsyncClient(timeout=12) as client:
                semaphore = asyncio.Semaphore(SYNC_METADATA_BATCH)
                await asyncio.gather(*(enrich_one(movie, client, semaphore) for movie in changed))
            state['enriched'] += len(selected)
            if state['enriched'] >= len(state['ids']):
                state['phase'] = 'completed'
        if state['phase'] == 'completed':
            state['status'] = 'completed'
        with db() as con:
            released = con.execute(text('''UPDATE sync_jobs SET state=:state,lease_id='',locked_until=0
                                   WHERE user_id=:user_id AND lease_id=:lease_id RETURNING user_id'''),
                                   {'state': json.dumps(state), 'user_id': user_id,
                                    'lease_id': lease_id}).scalar_one_or_none()
            if released is None:
                raise HTTPException(409, 'La carpeta cambió durante la sincronización. Volvé a sincronizar.')
            save_movies(con, user_id, changed)
            if cache_updates:
                values = []
                params = {'checked_at': int(time.time())}
                for index, (title_key, data) in enumerate(cache_updates):
                    values.append(f'(:key_{index},:data_{index},:checked_at)')
                    params[f'key_{index}'] = title_key
                    params[f'data_{index}'] = json.dumps(data)
                con.execute(text('INSERT INTO tmdb_cache(title_key,data,checked_at) VALUES ' + ','.join(values) +
                                 ' ON CONFLICT(title_key) DO UPDATE SET data=excluded.data,checked_at=excluded.checked_at'), params)
            if changed:
                params = {'user_id': user_id, **{f'file_{index}': movie['id'] for index, movie in enumerate(changed)}}
                selected = ','.join(f':file_{index}' for index in range(len(changed)))
                edits = {row['file_id']: json.loads(row['metadata']) for row in con.execute(text(
                    'SELECT file_id,metadata FROM edits WHERE user_id=:user_id AND file_id IN (' + selected + ')'), params).mappings()}
                response_movies = [{**movie, **edits.get(movie['id'], {})} for movie in changed]
            else:
                response_movies = []
            if state['status'] == 'completed':
                if not state['limited']:
                    params = {'user_id': user_id}
                    selected = ''
                    if state['ids']:
                        params.update({f'id_{index}': file_id for index, file_id in enumerate(state['ids'])})
                        selected = ' AND file_id NOT IN (' + ','.join(f':id_{index}' for index in range(len(state['ids']))) + ')'
                    con.execute(text('DELETE FROM movies WHERE user_id=:user_id' + selected), params)
                con.execute(text('UPDATE libraries SET synced_at=:synced_at WHERE user_id=:user_id'),
                            {'synced_at': int(time.time()), 'user_id': user_id})
        return {**sync_summary(state, library['folder_name']),
                'movies': response_movies}
    except BaseException:
        with db() as con:
            con.execute(text("UPDATE sync_jobs SET lease_id='',locked_until=0 WHERE user_id=:user_id AND lease_id=:lease_id"),
                        {'user_id': user_id, 'lease_id': lease_id})
        raise


class MovieEdits(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    year: str | None = Field(None, max_length=4, pattern=r'^(\d{4})?$')
    director: str | None = Field(None, max_length=200)
    country: str | None = Field(None, max_length=300)
    overview: str | None = Field(None, max_length=2500)
    genre: str | None = Field(None, max_length=100)
    poster: str | None = Field(None, max_length=1000)
    backdrop: str | None = Field(None, max_length=1000)
    collection: str | None = Field(None, max_length=100)
    featured: bool | None = None
    favorite: bool | None = None


@app.patch('/api/movies/{file_id}')
async def update_movie(request: Request, file_id: str, payload: MovieEdits):
    is_csrf_safe(request)
    user = await require_manager(request)
    validate_id(file_id)
    with db() as con:
        row = con.execute(text('SELECT 1 FROM movies WHERE user_id=:user_id AND file_id=:file_id'),
                          {'user_id': user['user_id'], 'file_id': file_id}).first()
        if not row:
            raise HTTPException(404,'Esta película no está en tu biblioteca')
        prev = con.execute(text('SELECT metadata FROM edits WHERE user_id=:user_id AND file_id=:file_id'),
                           {'user_id': user['user_id'], 'file_id': file_id}).mappings().first()
        data = json.loads(prev['metadata']) if prev else {}
        changes = payload.model_dump(exclude_unset=True)
        for key in ('poster','backdrop'):
            url = changes.get(key)
            if url and (urlparse(url).scheme != 'https' or not urlparse(url).hostname):
                raise HTTPException(422, 'Las imágenes deben tener URL HTTPS')
        data.update(changes)
        con.execute(text('''INSERT INTO edits(user_id,file_id,metadata) VALUES(:user_id,:file_id,:metadata)
                    ON CONFLICT(user_id,file_id) DO UPDATE SET metadata=excluded.metadata'''),
                    {'user_id': user['user_id'], 'file_id': file_id, 'metadata': json.dumps(data)})
    return {'ok':True}


def library_movie(user_id: str, file_id: str) -> dict:
    validate_id(file_id)
    with db() as con:
        row = con.execute(text('SELECT metadata FROM movies WHERE user_id=:user_id AND file_id=:file_id'),
                          {'user_id': user_id, 'file_id': file_id}).mappings().first()
    if not row:
        raise HTTPException(404, 'El archivo no pertenece a la biblioteca indexada')
    metadata = json.loads(row['metadata'])
    if not metadata.get('canDownload',True):
        raise HTTPException(403, 'El propietario deshabilitó la descarga')
    return metadata


async def subtitle_files(user: dict, file_id: str) -> list[dict]:
    movie = library_movie(user['user_id'], file_id)
    parent = movie.get('parentId')
    if not parent:
        info = await google_get(f'{DRIVE_URL}/{file_id}', user['access_token'],
                                {'fields': 'parents', 'supportsAllDrives': 'true'})
        parent = next(iter(info.get('parents', [])), None)
    if not parent:
        return []

    async def list_folder(folder_id):
        files = []
        page_token = None
        while True:
            result = await google_get(DRIVE_URL, user['access_token'], {
                'q': f"'{validate_id(folder_id)}' in parents and trashed = false",
                'fields': 'nextPageToken,files(id,name,mimeType,capabilities/canDownload)',
                'pageSize': 100, 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true',
                **({'pageToken': page_token} if page_token else {})})
            files.extend(result.get('files', []))
            page_token = result.get('nextPageToken')
            if not page_token:
                return files
            if len(files) >= 1000:
                raise HTTPException(422, 'La carpeta tiene demasiados archivos para asociar los subtítulos automáticamente')

    def normalized(value):
        return re.sub(r'[^a-z0-9]', '', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().casefold())

    files = await list_folder(parent)
    videos = [item for item in files if is_video(item)]
    title_key = normalized(' '.join(filename_title(movie.get('filename', ''))))
    same_movie = all(normalized(' '.join(filename_title(item['name']))) == title_key for item in videos)
    subfolders = [item for item in files if item.get('mimeType') == FOLDER_MIME and
                  normalized(item['name']) in {'subs', 'sub', 'subtitles', 'subtitulos'}]
    for folder in subfolders[:5]:
        files.extend(await list_folder(folder['id']))
    stem = normalized(Path(movie.get('filename', '')).stem)
    tracks = []
    for item in files:
        if Path(item['name']).suffix.lower() not in SUBTITLE_EXT:
            continue
        candidate = normalized(Path(item['name']).stem)
        matches = candidate.startswith(stem) if stem else False
        subtitle_key = normalized(' '.join(filename_title(item['name'])))
        if len(videos) <= 1 or same_movie or matches or subtitle_key == title_key:
            tracks.append(item)
    return sorted(tracks, key=lambda item: item['name'].casefold())


@app.get('/api/movies/{file_id}/subtitles')
async def subtitles(request: Request, file_id: str):
    user = await require_reader(request, credentials=True)
    tracks = await subtitle_files(user, file_id)
    return {'tracks': [{'id': track['id'], 'label': track['name'],
                        'src': f"/api/movies/{file_id}/subtitles/{track['id']}"}
                       for track in tracks if track.get('capabilities', {}).get('canDownload', True)]}


@app.api_route('/api/movies/{file_id}/subtitles/{subtitle_id}', methods=['GET', 'HEAD'])
async def subtitle_content(request: Request, file_id: str, subtitle_id: str,
                           offset: float = Query(0, ge=0, le=172800), cast_token: str = Query('', max_length=3000)):
    validate_id(subtitle_id)
    user = await playback_reader(request, file_id, cast_token)
    tracks = await subtitle_files(user, file_id)
    selected = next((track for track in tracks if track['id'] == subtitle_id), None)
    if not selected:
        raise HTTPException(404, 'El subtítulo no está asociado a esta película')
    if not selected.get('capabilities', {}).get('canDownload', True):
        raise HTTPException(403, 'El propietario deshabilitó la descarga del subtítulo')
    if request.method == 'HEAD':
        response = Response(media_type='text/vtt', headers={'Cache-Control': 'private, no-store'})
        del response.headers['content-length']
        return response
    content = bytearray()
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            async with client.stream('GET', f'{DRIVE_URL}/{subtitle_id}', params={'alt': 'media'},
                                     headers={'Authorization': f"Bearer {user['access_token']}"}) as response:
                if response.status_code != 200:
                    raise HTTPException(502, 'No se pudo leer el subtítulo desde Drive')
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > SUBTITLE_LIMIT:
                        raise HTTPException(413, 'El subtítulo supera el límite de 2 MB')
        converted = await asyncio.to_thread(subtitle_to_vtt, bytes(content), Path(selected['name']).suffix.lower(), offset)
    except (httpx.HTTPError, UnicodeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        raise HTTPException(502, 'No se pudo convertir el subtítulo a WebVTT') from exc
    return Response(converted, media_type='text/vtt',
                    headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@app.api_route('/api/play/{file_id}', methods=['GET', 'HEAD'])
async def compatible_stream(request: Request, file_id: str, start: float = Query(0, ge=0, le=172800),
                            cast_token: str = Query('', max_length=3000)):
    user = await playback_reader(request, file_id, cast_token)
    movie = library_movie(user['user_id'], file_id)
    duration = movie.get('durationSeconds') or movie.get('duration', 0) * 60
    if duration and start >= duration:
        raise HTTPException(416, 'La posición está fuera de la película')
    if request.method == 'HEAD':
        response = Response(media_type='video/mp4', headers={'Accept-Ranges': 'none', 'Cache-Control': 'private, no-store'})
        del response.headers['content-length']
        return response
    if TRANSCODE_SLOTS.locked():
        raise HTTPException(503, 'Las dos reproducciones compatibles del servidor están ocupadas. Probá de nuevo en un momento.')
    await TRANSCODE_SLOTS.acquire()
    process = None
    source = None
    stopped = False

    async def stop():
        nonlocal stopped
        if stopped:
            return
        stopped = True
        try:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.communicate()
        finally:
            try:
                if source is not None:
                    await asyncio.to_thread(source.close)
            finally:
                TRANSCODE_SLOTS.release()

    try:
        url = f'{DRIVE_URL}/{file_id}?alt=media&supportsAllDrives=true'
        source = DriveSource(url, user['access_token'])
        local_url = source.start()
        process = await asyncio.create_subprocess_exec(*compatible_command(local_url, start),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        first = await asyncio.wait_for(process.stdout.read(65536), timeout=30)
        if not first:
            raise HTTPException(502, 'No se pudo preparar el audio compatible desde Drive')
    except BaseException as exc:
        await asyncio.shield(stop())
        if isinstance(exc, (OSError, RuntimeError, TimeoutError)):
            raise HTTPException(502, 'No se pudo iniciar la reproducción compatible') from exc
        raise

    async def iterator():
        try:
            yield first
            while chunk := await process.stdout.read(65536):
                yield chunk
        finally:
            await asyncio.shield(stop())

    return StreamingResponse(iterator(), media_type='video/mp4', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff', 'Accept-Ranges': 'none'})


@app.api_route('/api/stream/{file_id}', methods=['GET', 'HEAD'])
async def stream(request: Request, file_id: str, download: bool = False, cast_token: str = Query('', max_length=3000)):
    user = await playback_reader(request, file_id, cast_token)
    metadata = library_movie(user['user_id'], file_id)
    range_header = request.headers.get('range')
    if range_header and not RANGE_RE.fullmatch(range_header):
        raise HTTPException(416, 'Solo se admite un único rango de bytes')
    if request.method == 'HEAD':
        response = Response(media_type=metadata.get('mime') or 'application/octet-stream',
                            headers={'Accept-Ranges': 'bytes', 'Cache-Control': 'private, no-store'})
        if metadata.get('size'):
            response.headers['Content-Length'] = str(metadata['size'])
        else:
            del response.headers['content-length']
        return response
    headers = {'Authorization': f"Bearer {user['access_token']}"}
    if range_header:
        headers['Range'] = range_header
    client = httpx.AsyncClient(timeout=httpx.Timeout(25,read=None),follow_redirects=True)
    try:
        upstream = await client.send(client.build_request('GET', f'{DRIVE_URL}/{file_id}',
            params={'alt':'media','supportsAllDrives':'true'},headers=headers), stream=True)
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(502, 'Google Drive no pudo transmitir la película') from exc
    if upstream.status_code not in (200,206):
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status if status in (400,403,404,416,429) else 502,
                            'No fue posible reproducir el archivo desde Drive')
    forwarded = {name:upstream.headers[name] for name in
                 ('content-type','content-length','content-range') if name in upstream.headers}
    forwarded.update({'Accept-Ranges':'bytes','Cache-Control':'private, no-store',
                      'X-Content-Type-Options':'nosniff'})
    if download:
        filename = str(metadata.get('filename') or file_id)
        forwarded['Content-Disposition'] = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    async def iterator():
        try:
            async for chunk in upstream.aiter_raw(256*1024):
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
    return StreamingResponse(iterator(), status_code=upstream.status_code, headers=forwarded)


@app.get('/health')
async def health():
    with db() as con:
        con.execute(text('SELECT 1'))
    return {'status':'ok'}


@app.api_route('/api/{route:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'], include_in_schema=False)
@app.api_route('/auth/{route:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'], include_in_schema=False)
async def unknown_endpoint(route: str):
    raise HTTPException(404)


app.frontend('/', directory=str(DIST), fallback='index.html', check_dir=False)
