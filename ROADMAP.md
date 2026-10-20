# Hoja de ruta — Cinemateca

El README describe el comportamiento implementado y cómo ejecutarlo. Esta hoja separa lo disponible de lo que todavía requiere desarrollo o validación.

## Implementado

- [x] Backend FastAPI con Python 3.13, dependencias en `pyproject.toml` y `uv.lock`.
- [x] Frontend estático React/TypeScript/Vite servido por FastAPI, sin servidor Node en producción.
- [x] SQLite para desarrollo y libSQL remoto en Turso para producción, con sesiones y tokens OAuth cifrados.
- [x] OAuth Google con PKCE, `state`, renovación de acceso y comprobación del permiso `drive.readonly`.
- [x] Selección de carpeta y exploración de subcarpetas; sincronización por pasos con cursor y reserva temporal persistidos.
- [x] Reintentos acotados de sincronización ante errores de red/hosting y de inicialización ante fallos temporales de DNS/conexión.
- [x] Catálogo real, sin portada comercial: pósters grandes, búsqueda, filtros por género y orden por título, año o popularidad TMDB.
- [x] Países, dirección, reparto principal y sinopsis desde TMDB; caché versionada y edición manual de metadatos.
- [x] Versiones agrupadas por película, con selección del archivo en la ficha y sin borrar originales.
- [x] Fichas en `/peliculas/{file_id}`, con carga directa, reproducción y edición en la página; Cast visible al detectar dispositivos.
- [x] Reproducción y descarga del original con rangos HTTP; nombre del archivo enlazado a Drive.
- [x] Modo compatible con AAC estéreo y video sin recodificar, barra temporal y límite de dos conversiones simultáneas por proceso.
- [x] Subtítulos externos SRT/VTT/ASS/SSA, conversión a WebVTT, carga automática y selector para cambiarlos o apagarlos.
- [x] Integración de Google Cast con receptor estándar, controles remotos y enlaces temporales por película; validación con receptor simulado.
- [x] Acceso abierto sin Google, estrictamente de lectura; administración, favoritos, edición y sincronización reservados al propietario.
- [x] Credenciales de la biblioteca publicada independientes de la sesión del navegador; revocación de acceso y protección al cambiar de carpeta.
- [x] Pruebas con SQLite/libSQL, permisos públicos/privados, OAuth, CSRF, rangos, subtítulos, TMDB y conversión real de audio.
- [x] Configuración de ejemplo sin secretos ni carpeta personal predefinida, y exclusión de bases, credenciales y artefactos locales de Git.

## Verificado y límites actuales

- Verificado con Drive real: OAuth, carpeta compartida, sincronización reanudable, catálogo público, rechazo de escrituras anónimas y descarga por rangos.
- Verificado en navegador: agrupación y elección de versiones, fichas con créditos, audio AAC y subtítulos externos activables/desactivables; grilla y modales en móvil sin desbordamiento horizontal.
- No hay recodificación de video: HEVC u otros códecs siguen dependiendo del navegador. La conversión de audio no garantiza compatibilidad de cualquier archivo.
- No se extraen subtítulos incrustados en MKV ni se eliminan subtítulos quemados en la imagen.
- La sincronización continúa en el servidor sin una pestaña abierta; tras una suspensión del hosting retoma el cursor guardado. Se limita a cinco niveles y 2000 elementos.
- Los subtítulos externos se limitan a 2 MB y deben poder asociarse a la película. Se respetan las restricciones de descarga de Drive.
- Un build subido no equivale a despliegue listo: comprobar su estado final y `/health`. Se observaron fallos temporales de DNS hacia Turso durante la verificación de réplicas en FastAPI Cloud; el reintento no reemplaza resolver una caída persistente del proveedor.

## Próxima validación

- [ ] Reproducción completa de películas grandes, saltos prolongados y reconexiones en distintos navegadores y dispositivos.
- [ ] Chromecast físico: MP4 fragmentado desde MKV, códecs por modelo, pausas prolongadas, subtítulos, búsquedas y reconexión del control.
- [ ] Concurrencia, consumo de CPU/memoria, cuotas de Drive y comportamiento del hosting bajo carga.
- [ ] Confirmación de despliegues nuevos y recuperación tras fallos DNS del hosting, conservando la biblioteca en Turso.
- [ ] Pruebas automatizadas de navegador y revisión de accesibilidad: teclado, foco de modales, lectores de pantalla y controles del reproductor.
- [ ] Respaldo y restauración de Turso con la clave de cifrado almacenada por separado.

## Mejoras posteriores

- [ ] Continuidad de reproducción y preferencias de idioma/subtítulos.
- [ ] Extracción de pistas incrustadas y selección de pistas de audio.
- [ ] Evaluar transcodificación de video separada del servidor web cuando el navegador no admita el códec original.
- [x] Continuación de sincronizaciones pendientes en el servidor, sin una pestaña abierta.
- [ ] Sincronización incremental de cambios de Drive.
- [ ] Búsqueda avanzada, etiquetas personales y precarga.
- [ ] Observabilidad y límites de solicitudes para el acceso público.
- [ ] Automatización de pruebas y despliegue con secretos configurados fuera del repositorio.
- [ ] Revisar si `drive.file` puede reemplazar a `drive.readonly` si cambia el flujo de selección de carpetas.

## TV Samsung Tizen

- [ ] Navegación por foco y control remoto.
- [ ] Compatibilidad de códecs, subtítulos, pantalla completa y rendimiento en un televisor o SDK real.
- [ ] Manifiesto y empaquetado Tizen después de validar el reproductor web.
