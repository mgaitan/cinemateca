# Cinemateca

Cinemateca personal con películas de una carpeta compartida de Google Drive. React + TypeScript + Vite genera un **frontend estático servido por FastAPI**, junto con la API, OAuth y el proxy de video, en el mismo origen. No hay servidor Node en producción.

Aplicación: **https://cinemateca.fastapicloud.dev**. Código: [mgaitan/cinemateca](https://github.com/mgaitan/cinemateca). Pendientes y alcance: [ROADMAP.md](ROADMAP.md).

El propietario autoriza Google y elige su carpeta. Puede configurar `DEFAULT_DRIVE_FOLDER_URL` para precargarla; el repositorio no incluye una carpeta personal por defecto. Con acceso abierto, los visitantes pueden navegar, reproducir y descargar sin iniciar sesión.

## Funcionalidades

- Grilla de pósters grandes, búsqueda por título, año, dirección, país y reparto; filtros por género y orden por título, año o popularidad TMDB.
- Fichas con países, dirección, reparto principal y sinopsis; versiones de una misma película agrupadas bajo un solo póster.
- Reproducción original o con audio AAC estéreo, descarga del original y enlace del archivo a Drive.
- Subtítulos externos con carga automática, conversión a WebVTT, selección de pista y opción de desactivarlos.
- Publicación abierta de solo lectura; edición, favoritos, sincronización y administración reservados al propietario.

## Desarrollo con uv

Necesitás [uv](https://docs.astral.sh/uv/) y Node.js 24 LTS. Python **3.13** está fijado por compatibilidad con el driver de libSQL. `uv` descarga Python y crea `.venv` automáticamente. Las dependencias se declaran en `pyproject.toml` y se fijan en `uv.lock`; no se usan archivos `requirements.txt`.

Todos los comandos parten de la raíz del repositorio:

```bash
git clone https://github.com/mgaitan/cinemateca.git
cd cinemateca
uv sync --locked
npm --prefix frontend ci
```

Si todavía no tenés configuración local, copiá `backend/.env.example` a `backend/.env`. **No sobrescribas un archivo existente con credenciales.** La app carga ese archivo al importar el backend; las variables del entorno tienen prioridad.

Generá una clave una sola vez y guardala como `TOKEN_ENCRYPTION_KEY`:

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

En una terminal ejecutá `uv run fastapi dev`; en otra, `npm --prefix frontend run dev`. Abrí http://localhost:5173. Vite envía `/api`, `/auth` y `/health` a FastAPI en el puerto 8000. El catálogo usa la biblioteca conectada o la publicada por su propietario, sin películas de demostración.

Para probar los estáticos servidos por FastAPI:

```bash
npm --prefix frontend run build
uv run fastapi run
```

Abrí http://localhost:8000. FastAPI sirve también `/assets/*`, `/demo/*` y el fallback de la SPA; las rutas inexistentes bajo `/api/*` o `/auth/*` devuelven errores JSON.

## Google Drive y TMDB

1. En Google Cloud Console, habilitá **Google Drive API** y creá un cliente OAuth de tipo **Web application**.
2. Configurá la pantalla de consentimiento y los usuarios autorizados para probar la app.
3. Registrá como **Authorized redirect URIs** tanto `http://localhost:8000/auth/callback` como `https://cinemateca.fastapicloud.dev/auth/callback`.
4. Configurá `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` y `TOKEN_ENCRYPTION_KEY`.
5. En desarrollo usá `GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback`, `WEB_REDIRECT=http://localhost:5173` y `COOKIE_SECURE=false`.
6. Entrá a **Conectar Drive**, autorizá Google y elegí una carpeta a la que esa cuenta tenga acceso. Si configuraste `DEFAULT_DRIVE_FOLDER_URL`, aparecerá precargada.

El acceso a la carpeta depende de la cuenta conectada. Se usa `drive.readonly`, con OAuth PKCE y `state`; los tokens quedan cifrados con Fernet en la base y nunca se envían al frontend. Google puede exigir verificación para ofrecer este scope a terceros; revisá las condiciones de publicación y vencimiento de tokens en tu consola OAuth.

En el consentimiento de Google, marcá el permiso para **ver y descargar los archivos de Google Drive**: iniciar sesión con el perfil no concede ese acceso automáticamente. Si falta, la app ofrece **Volver a autorizar Drive**; el callback no crea una sesión nueva sin ese permiso.

`TMDB_API_TOKEN` es opcional: un **API Read Access Token** de [TMDB](https://www.themoviedb.org/settings/api) permite completar imágenes, sinopsis, géneros, popularidad, países de producción, dirección y los seis primeros integrantes del reparto. La búsqueda identifica la película y una consulta de detalles con `append_to_response=credits` obtiene países y créditos. Los metadatos también se pueden editar manualmente.

## Acceso abierto, solo lectura

En **Administrar**, el propietario puede activar o desactivar **Acceso abierto (solo lectura)**. La biblioteca publicada se guarda en `public_library`, junto con una copia cifrada de las credenciales renovables de Drive, independiente de la cookie de sesión. Cerrar sesión no interrumpe el acceso de los visitantes. Solo el propietario registrado puede administrar esa publicación; otra cuenta de Google no puede reemplazarla.

Los visitantes pueden consultar el catálogo, reproducir videos y descargar originales y subtítulos asociados. No pueden editar, modificar favoritos, cambiar la carpeta, sincronizar ni gestionar el acceso. Los endpoints verifican esas restricciones: no dependen de ocultar botones. El perfil público no expone correo, identidad ni credenciales del propietario. Los enlaces directos a Drive mantienen los permisos propios de Google; no se modifican permisos de archivos ni carpetas.

El propietario puede revocar el acceso desde el mismo botón. Cambiar la carpeta desactiva la publicación hasta que la vuelva a habilitar explícitamente. Si Google revoca el refresh token, debe volver a autorizar Drive. La publicación usa las cuotas de Drive y recursos del servidor del propietario, y cualquiera que conozca la URL puede acceder.

## Persistencia: SQLite y Turso

Sin `TURSO_DATABASE_URL`, el backend usa SQLite local (`SQLITE_PATH`, por defecto `backend/data/cinemateca.sqlite3`). Se conserva el esquema original y no se migran ni borran bases existentes.

En FastAPI Cloud se usa **libSQL remoto en Turso**: sesiones, catálogo, favoritos, ediciones y caché sobreviven a reemplazos de réplicas. Las películas siguen en Drive. No se necesita PostgreSQL ni una réplica local de la base.

La [documentación oficial de Turso](https://docs.turso.tech/sdk/python/quickstart) distingue bases **libSQL** de bases del nuevo motor **Turso**. Esta app usa `sqlalchemy-libsql`: creá una base **libSQL**, sin `--tursodb`.

```bash
turso auth login
turso db create cinemateca
turso db show cinemateca --url
turso db tokens create cinemateca
```

Guardá la URL y el token en `TURSO_DATABASE_URL` y `TURSO_AUTH_TOKEN`. La app crea las tablas al arrancar. No se transfiere automáticamente el contenido de un SQLite anterior: si tiene datos que querés conservar, importalo antes de usar una base remota nueva.

Mantené `TOKEN_ENCRYPTION_KEY` estable entre despliegues: cambiarla impide descifrar las sesiones existentes. Hacé backups y guardá la clave separada de la base.

## FastAPI Cloud

El proyecto Python y el directorio de aplicación están en la **raíz**, con `backend.main:app` como entrypoint. No selecciones `backend/` como application directory.

```bash
uv run fastapi login
uv run fastapi cloud whoami
uv run fastapi cloud link
```

Vinculá la app existente `cinemateca`. Las credenciales y el vínculo local quedan fuera de git.

Configurá en **Environment Variables** de la app los valores de `backend/.env.production.example`:

| Variable | Valor de producción |
| --- | --- |
| `APP_ENV` | `production` |
| `TURSO_DATABASE_URL` | URL `libsql://…` de la base |
| `TURSO_AUTH_TOKEN` | Token de acceso a la base |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Credenciales OAuth web |
| `GOOGLE_REDIRECT_URI` | `https://cinemateca.fastapicloud.dev/auth/callback` |
| `WEB_REDIRECT` | `/` |
| `COOKIE_SECURE` | `true` |
| `TOKEN_ENCRYPTION_KEY` | Clave Fernet estable |
| `DEFAULT_DRIVE_FOLDER_URL` | Enlace de la carpeta compartida |
| `TMDB_API_TOKEN` | Opcional |

`backend/.env` es para ejecutar localmente: **no se sube ni configura el cloud automáticamente**. Los secretos se cargan en el entorno de FastAPI Cloud. `SQLITE_PATH` se ignora cuando hay Turso configurado.

Antes de cada despliegue:

```bash
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run pytest -q
uv run fastapi deploy
```

FastAPI Cloud recibe el build estático ya generado; no ejecuta el build de Vite. `.fastapicloudignore` incluye `frontend/dist/` aunque esté ignorado por git y excluye configuraciones locales y secretos. Es el [flujo oficial de despliegue full stack](https://fastapicloud.com/docs/builds-and-deployments/frontend/).

Con `APP_ENV=production`, el arranque valida Turso, credenciales OAuth, clave de cifrado, redirect HTTPS, cookies seguras y frontend compilado. `/health` comprueba la conexión a la base. La inicialización reintenta hasta cinco veces los errores temporales de DNS o conexión, con esperas crecientes; errores permanentes de configuración no se ocultan.

Después del despliegue verificá `/health`, pósters, búsqueda, categorías por género, orden por popularidad, login Google, sincronización y reproducción con búsqueda temporal.

## Sincronización por pasos

`POST /api/sync?start=true` inicia una exploración nueva o reanuda una que ya está en curso. Cada paso procesa hasta 10 páginas de distintas carpetas de Drive (100 elementos por página, consultadas en paralelo) o un lote de hasta 10 búsquedas TMDB. El servidor continúa los trabajos pendientes cada cinco segundos y el navegador consulta el avance, incluso para visitantes del catálogo público. Las lecturas y escrituras de la caché TMDB también se agrupan para reducir los viajes a la base remota.

El catálogo, el cursor y el avance se guardan en Turso sin vencimiento: volver a entrar no inicia otra exploración. Una tarea del proceso continúa las sincronizaciones autorizadas usando las credenciales guardadas, aunque se cierre la pestaña. Si el hosting suspende o reemplaza el proceso, el siguiente arranque recupera el cursor. Una reserva temporal en la base evita procesar el mismo paso desde dos réplicas a la vez. El servidor reintenta los trabajos pendientes después de errores; si Google revoca el acceso, el propietario debe volver a autorizar Drive. El navegador reintenta hasta ocho veces los fallos temporales de lectura, con esperas crecientes de hasta 15 segundos.

El catálogo anterior permanece disponible durante la exploración; los archivos ausentes se eliminan al terminar correctamente. Si se alcanza un límite de exploración, se informa en la interfaz y se conservan los archivos que no se alcanzaron a revisar. Reconfirmar la misma carpeta no borra el catálogo ni reinicia el avance.

Las categorías son géneros de TMDB (Drama, Comedia, etc.) cuando hay coincidencias; no se usan las carpetas como filtro. Se puede ordenar por título, año o popularidad TMDB, dejando al final las películas sin ese dato. La popularidad es un puntaje de actividad, no una calificación; se actualiza al sincronizar con una caché de hasta 24 horas. La caché tiene versión para completar campos nuevos en la siguiente sincronización. El propietario puede editar géneros, títulos y pósters desde la ficha. Los favoritos y las ediciones se conservan entre sincronizaciones.

Los prefijos de enumeración como `38. Antigone (2019).mkv` se omiten al buscar títulos. Los cambios de versión de metadatos actualizan la biblioteca guardada en segundo plano, sin volver a explorar Drive, y respetan las ediciones manuales.

La grilla agrupa archivos por ID de TMDB, o por título y año cuando falta ese ID. Cada película muestra un solo póster, año, países y cantidad de versiones. En la ficha se puede elegir el archivo/edición, con su nombre, subcarpeta, duración y tamaño. Reproducir, descargar y abrir Drive usan la versión elegida; los originales no se borran ni se fusionan.

`GET /api/movies` devuelve páginas de 16 películas agrupadas con todas sus versiones. Acepta `offset`, `limit`, `q`, `genre`, `favorite` y `sort` (`title`, `year`, `popularity`), e informa `count`, `genres` y `next_offset`. Los filtros y el orden se aplican a toda la biblioteca antes de paginar. El navegador solicita la siguiente página al acercarse al final del listado; también ofrece un botón para cargar más. El servidor lee el índice guardado para armar las páginas, sin consultar Drive.

## Reproducción, sonido y subtítulos

El modo predeterminado **Compatible (AAC estéreo)** convierte el audio en el servidor y remultiplexa el video sin recodificarlo a MP4 fragmentado. Resuelve, por ejemplo, archivos H.264/E-AC-3 que mostraban imagen pero no tenían audio audible en el navegador. El video conservado debe ser compatible con el navegador; no se implementa recodificación general de HEVC/AV1/etc. También se puede elegir **Original**, que transmite los bytes del archivo sin conversión.

`imageio-ffmpeg` incluye el ejecutable de FFmpeg en la instalación con uv; no se descarga durante la reproducción. La lectura HTTPS autenticada de Drive la realiza httpx y FFmpeg recibe los bytes mediante un relay HTTP de loopback con ruta aleatoria, acceso por rangos y sin tokens en los argumentos del proceso. Esto evita la falla TLS del ejecutable estático distribuido. No se guarda una copia de la película en disco. Los procesos se cierran al desconectarse el reproductor; hay un máximo de dos conversiones simultáneas por proceso de aplicación.

`GET /api/play/{file_id}?start=segundos` transmite MP4/AAC. El reproductor usa una barra temporal que reinicia el stream en la posición elegida, porque el MP4 fragmentado no representa un archivo estático al que se puedan aplicar rangos arbitrarios. El modo Original conserva la búsqueda temporal nativa por `Range`.

Los subtítulos externos **SRT, VTT, ASS y SSA** de la misma carpeta o de una subcarpeta `Subs`, `Subtitles` o `Subtítulos` se descubren al abrir el reproductor. Si hay varias películas diferentes en la carpeta, se asocian por nombre; si solo hay una película o versiones de la misma, se ofrecen los subtítulos de esa carpeta. Se leen desde Drive con las credenciales del servidor y se convierten a WebVTT, con soporte de UTF-8, UTF-16 y Windows-1252. Solo se sirven archivos asociados a una película indexada, respetando `canDownload` y con límite de 2 MB por subtítulo.

Se activa automáticamente la primera pista identificada como español, o la primera disponible. El selector permite cambiarla o elegir **Desactivados**. Al saltar en el modo compatible se ajustan los tiempos del subtítulo. Si la lectura o conversión falla, se informa en el reproductor. No se extraen pistas incrustadas de MKV; los subtítulos quemados en la imagen no se pueden desactivar.

En la ficha, el nombre de archivo abre su página de Google Drive y **Descargar** entrega el original con su nombre, a través del servidor y sin exponer tokens. `GET /api/stream/{file_id}?download=true` agrega `Content-Disposition: attachment` y conserva los rangos. Tanto las descargas como los subtítulos y la reproducción exigen una sesión válida o una publicación habilitada y se limitan a la biblioteca indexada.

## Verificación y límites

```bash
uv run pytest -q
npm --prefix frontend run build
```

Los tests comprueban títulos, enlaces, rangos, catálogo, aislamiento entre usuarios, publicación y revocación de acceso, restricciones de escritura, renovación de credenciales sin sesión, subtítulos, créditos TMDB, cifrado, CSRF, estado OAuth y caché con SQLite y el driver libSQL. Una prueba genera audio E-AC-3 y comprueba su conversión real a AAC estéreo no silencioso. Compilá el frontend antes para comprobar también los estáticos. Las pruebas usan bases temporales, sin tocar el catálogo real.

El backend transmite desde Drive sin copiar películas al disco y reenvía `Range` para búsquedas cuando Google lo respeta. Hay que verificar permisos, cuotas, videos grandes y límites del hosting con la carpeta real. La sincronización recorre hasta 5 niveles y 2000 elementos y busca metadatos para todas las películas encontradas, en lotes pequeños. Avanza mientras el proceso del servidor esté activo; tras una suspensión del hosting retoma el cursor guardado.

`preview.html` conserva el prototipo anterior como demo autónoma, fuera del despliegue. Imágenes y metadatos externos conservan sus condiciones de uso.

## Qué se publica en GitHub

Se versionan código, pruebas, documentación, prototipo, recursos estáticos de demostración, `pyproject.toml`, `uv.lock` y el lockfile de npm. Los archivos `.env.example` contienen únicamente nombres de variables y valores de ejemplo: no otorgan acceso a Google, TMDB ni Turso.

No se versionan archivos `.env` reales, claves privadas, credenciales JSON, bases SQLite, sesiones, tokens, logs, `.fastapicloud/`, entornos virtuales, dependencias instaladas ni builds generados. La base Turso, las credenciales cifradas y las películas de Drive no forman parte del repositorio. No agregues esos archivos con `git add -f`; para desplegar, cargá los secretos por separado en el hosting.
