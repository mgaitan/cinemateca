# Cinemateca

Cinemateca personal con películas de una carpeta compartida de Google Drive. React + TypeScript + Vite genera un **frontend estático servido por FastAPI**, junto con la API, OAuth y el proxy de video, en el mismo origen. No hay servidor Node en producción.

Aplicación: **https://cinemateca.fastapicloud.dev**. Código: [mgaitan/cinemateca](https://github.com/mgaitan/cinemateca). Pendientes y alcance: [ROADMAP.md](ROADMAP.md).

El propietario autoriza Google y elige su carpeta. Puede configurar `DEFAULT_DRIVE_FOLDER_URL` para precargarla; el repositorio no incluye una carpeta personal por defecto. Con acceso abierto, los visitantes pueden navegar, reproducir y descargar sin iniciar sesión.

## Funcionalidades

- Grilla de pósters grandes, búsqueda por título, año, dirección, país y reparto; filtros por género y orden por título, año o popularidad TMDB.
- Fichas con URL propia, países, dirección, reparto principal y sinopsis; versiones de una misma película agrupadas bajo un solo póster.
- Reproducción original o con audio AAC estéreo, descarga del original y enlace del archivo a Drive.
- Google Cast desde la ficha o el reproductor: reproducción en Chromecast con pausa, volumen, búsqueda temporal y subtítulos externos.
- Subtítulos externos con carga automática, conversión a WebVTT, selección de pista y opción de desactivarlos.
- Publicación abierta de solo lectura; edición, favoritos, sincronización y administración reservados al propietario.

## Desarrollo con uv

Necesitás [uv](https://docs.astral.sh/uv/) y Node.js 24 LTS. Python **3.13** está fijado por compatibilidad con el driver de libSQL. La [documentación de FastAPI Cloud](https://fastapicloud.com/docs/builds-and-deployments/install-dependencies/) declara soporte para versiones mantenidas de Python y selección mediante `requires-python` o `.python-version`, pero la app todavía no migra a 3.15: la resolución de dependencias para esa versión falla porque `sqlalchemy-libsql` requiere `libsql-experimental`, sin wheels compatibles con CPython 3.15. El paquete fuente actual usa PyO3 0.19; actualizar Python requiere resolver primero esa dependencia nativa y volver a validar el driver. `uv` descarga Python y crea `.venv` automáticamente. Las dependencias se declaran en `pyproject.toml` y se fijan en `uv.lock`; no se usan archivos `requirements.txt`.

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

Cada póster y título enlaza a `/peliculas/{file_id}`. La ficha se abre como página, admite recarga, apertura en otra pestaña y enlaces compartidos; `GET /api/movies/{file_id}` recupera la película y todas sus versiones desde el índice guardado aunque no estén en la página actual del catálogo. Cambiar de versión navega a la URL de ese archivo. La reproducción y la edición aparecen dentro de la misma página; los permisos de lectura y administración se conservan.

`GET /api/movies` devuelve páginas de 16 películas agrupadas con todas sus versiones. Acepta `offset`, `limit`, `q`, `genre`, `favorite` y `sort` (`title`, `year`, `popularity`), e informa `count`, `genres` y `next_offset`. Los filtros y el orden se aplican a toda la biblioteca antes de paginar. El navegador solicita la siguiente página al acercarse al final del listado; también ofrece un botón para cargar más. El servidor lee el índice guardado para armar las páginas, sin consultar Drive.

## Reproducción, sonido y subtítulos

El modo predeterminado **Compatible (AAC estéreo)** convierte el audio en el servidor y remultiplexa el video sin recodificarlo a MP4 fragmentado. Resuelve, por ejemplo, archivos H.264/E-AC-3 que mostraban imagen pero no tenían audio audible en el navegador. El video conservado debe ser compatible con el navegador; no se implementa recodificación general de HEVC/AV1/etc. También se puede elegir **Original**, que transmite los bytes del archivo sin conversión.

`imageio-ffmpeg` incluye el ejecutable de FFmpeg en la instalación con uv; no se descarga durante la reproducción. La lectura HTTPS autenticada de Drive la realiza httpx y FFmpeg recibe los bytes mediante un relay HTTP de loopback con ruta aleatoria, acceso por rangos y sin tokens en los argumentos del proceso. Esto evita la falla TLS del ejecutable estático distribuido. No se guarda una copia de la película en disco. Los procesos se cierran al desconectarse el reproductor; hay un máximo de dos conversiones simultáneas por proceso de aplicación.

`GET /api/play/{file_id}?start=segundos` transmite MP4/AAC. El reproductor usa una barra temporal que reinicia el stream en la posición elegida, porque el MP4 fragmentado no representa un archivo estático al que se puedan aplicar rangos arbitrarios. El modo Original conserva la búsqueda temporal nativa por `Range`.

Los subtítulos externos **SRT, VTT, ASS y SSA** de la misma carpeta o de una subcarpeta `Subs`, `Subtitles` o `Subtítulos` se descubren al abrir el reproductor. Si hay varias películas diferentes en la carpeta, se asocian por nombre; si solo hay una película o versiones de la misma, se ofrecen los subtítulos de esa carpeta. Se leen desde Drive con las credenciales del servidor y se convierten a WebVTT, con soporte de UTF-8, UTF-16 y Windows-1252. Solo se sirven archivos asociados a una película indexada, respetando `canDownload` y con límite de 2 MB por subtítulo.

Se activa automáticamente la primera pista identificada como español, o la primera disponible. El selector permite cambiarla o elegir **Desactivados**. Al saltar en el modo compatible se ajustan los tiempos del subtítulo. Si la lectura o conversión falla, se informa en el reproductor. No se extraen pistas incrustadas de MKV; los subtítulos quemados en la imagen no se pueden desactivar.

En la ficha, el nombre de archivo abre su página de Google Drive y **Descargar** entrega el original con su nombre, a través del servidor y sin exponer tokens. `GET /api/stream/{file_id}?download=true` agrega `Content-Disposition: attachment` y conserva los rangos. Tanto las descargas como los subtítulos y la reproducción exigen una sesión válida o una publicación habilitada y se limitan a la biblioteca indexada.

## Chromecast

**Reproducir en Chromecast** aparece en la ficha cuando el SDK detecta dispositivos disponibles o una sesión Cast activa. El botón inicia la película en el receptor sin abrir el reproductor local. Desde el reproductor se transfiere la posición, el modo de audio y el subtítulo elegido. El servidor entrega los bytes al Chromecast; el celular o la computadora solo controla la reproducción. Navegar a otra ficha o cerrar la pestaña no envía una orden de detener. Al volver al mismo origen, el SDK intenta recuperar la sesión.

La integración usa el [Web Sender SDK y Default Media Receiver de Google](https://developers.google.com/cast/docs/web_sender/integrate), sin registrar una aplicación receptora propia. Abrí la app por HTTPS en un navegador compatible con Google Cast, con acceso al dispositivo y permiso de red local si el navegador lo solicita. El Chromecast debe poder alcanzar la URL del servidor; `localhost` apunta al propio receptor y no sirve para reproducir desde otro dispositivo.

El modo predeterminado transmite MP4 fragmentado con AAC estéreo, usando la conversión existente. Un MKV se remultiplexa en el servidor, sin descargarlo completo ni volver a codificar su video. H.264 suele ser la opción más compatible; HEVC/H.265 requiere un modelo que lo soporte. Los contenedores, perfiles y resoluciones admitidos dependen del [modelo de Chromecast](https://developers.google.com/cast/docs/media). En modo Original se envía el archivo tal cual; MKV no es un contenedor admitido por el receptor estándar. Los subtítulos incrustados siguen sin extraerse.

El panel de Cast permite pausar, reanudar, detener, ajustar volumen, seleccionar subtítulos externos y buscar una posición. En modo compatible, la búsqueda vuelve a cargar el stream desde el instante indicado y ajusta los tiempos WebVTT; requiere conocer la duración. En modo Original se usa la búsqueda nativa del receptor. Los controles desaparecen al terminar la sesión; cerrar un modal no la termina.

`POST /api/movies/{file_id}/cast` entrega un enlace de autorización cifrado y autenticado con vencimiento de 12 horas, limitado a una película y su carpeta. El receptor lo envía como `cast_token` a los endpoints de video y subtítulos, sin cookies ni credenciales de Google. Cada petición comprueba la biblioteca y los permisos actuales. Los enlaces privados dependen de la sesión que los creó; cerrar sesión los revoca para peticiones nuevas. Los públicos dependen de que siga habilitada la publicación. Cambiar de carpeta o deshabilitar descargas también impide nuevos accesos. Las rutas de medios ofrecen CORS, preflight y HEAD para el receptor; estos enlaces no autorizan consultas del catálogo ni escrituras. Las lecturas del catálogo y las fichas también ofrecen CORS sin credenciales para el cliente Tizen: la biblioteca privada sigue exigiendo sesión y no se habilitan escrituras entre orígenes.

La integración se comprobó en navegador con un SDK/receptor simulado y con pruebas de API y conversión real de MKV/E-AC-3 a MP4/AAC. Queda pendiente verificar reproducción, pausas prolongadas, reconexión y búsquedas con un Chromecast físico y archivos reales. Siguen aplicando los límites del hosting y de dos conversiones simultáneas por proceso.

## Prototipo Samsung Tizen

`tizen/` contiene una aplicación web empaquetada para televisores Samsung, separada del frontend de escritorio y escrita sin dependencias de JavaScript. Consume el catálogo público de esta instancia, en páginas de 16, con búsqueda, fichas, versiones y navegación por foco. Necesita **Acceso abierto**: no implementa login Google ni administración desde el televisor.

La reproducción usa [Samsung AVPlay](https://developer.samsung.com/smarttv/develop/api-references/samsung-product-api-references/avplay-api.html). **Reproducir original** envía el archivo de Drive a la TV a través del servidor, con búsqueda temporal y selección de pistas de subtítulos incrustadas que AVPlay reconozca. **Audio compatible (AAC)** usa el remultiplexado MP4 del backend y reinicia el stream al buscar otra posición; este modo no incluye pistas incrustadas. No se recodifica el video. Los formatos admitidos dependen del modelo de TV; los subtítulos externos y su descarga a almacenamiento Tizen quedan pendientes.

Para generar el paquete, sin instalar Tizen Studio:

```bash
uv run python tizen/build.py
```

El archivo generado es `tizen/dist/cinemateca.wgt`, un paquete sin firma que contiene únicamente los cuatro recursos de la app, sin credenciales. [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) admite paquetes `.wgt` propios y gestiona su firma e instalación. En el televisor, habilitá Developer Mode; en Apps2Samsung elegí el dispositivo y el paquete generado, y completá la configuración de certificados que solicite. El instalador requiere acceso por red al TV. El manifiesto declara Tizen 2.3 como mínimo para las APIs usadas, sin afirmar compatibilidad verificada con todos los modelos desde esa versión.

El control usa flechas y Enter para navegar, Volver para cerrar el reproductor o regresar al catálogo, y las teclas multimedia disponibles para pausa/reanudación y saltos de 30 segundos. Los controles se ocultan después de seis segundos y reaparecen al usar el mando. En la raíz, Volver sale de la aplicación. Ocultar o cerrar la app libera AVPlay.

Validado con catálogo de prueba y AVPlay simulado: paginación 16→32, fichas, reproducción original, pausa, saltos, selección de subtítulos, remultiplexado desde otra posición y regreso con recuperación del foco. **Pendiente** instalar en un TV físico y comprobar modelo/Tizen, certificados, HTTPS, códecs, subtítulos y reproducción prolongada. El `.wgt` y sus fuentes no forman parte del despliegue de FastAPI Cloud.

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
