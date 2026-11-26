# H2.2 — Engine real (Eugenia-001)

BRAIN #18; contrato temprano #17. Reutilización de local_video.py + video_recipe.py
de Montara 1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d y ExportStage de
Zucker Editor 2.6.20, 9af275b18535005e36bd289a68ceb4a1d2f6c506.
Los repositorios públicos son referencias externas fijadas; no se vende ni
distribuye Engine, modelos o binarios en este PR. Ausencia de LICENSE general
no otorga licencia de redistribución: revisión de titularidad/licencias antes
de release. El workflow solo ejecuta componentes autorizados en scratch.
No se modifica V1 ni sus submódulos.

Un plan revisado no debe volver a calcularse con Ingest/Sync/Edit. El adaptador
traduce los shots v0 a receta Stage privada; llama al runner nativo real que
invoca ExportStage. No VideoJobs, portal, DB, cuotas, auth, publicación ni Mixer.
Perfil único 1080p/30 H264/AAC MP4, intervalos de frame exacto, velocidad 1,
máster continuo. Core materializa originales autorizados y workspace exclusivo;
el adapter verifica SHA256 completo de las copias Core y las usa directamente, evita sobrescribir intentos,
usa proceso aislado (no sandbox de seguridad por sí mismo) y devuelve candidato
privado. Core debe verificar fencing/publicación y permisos; no confundir
candidato con éxito. No se implementa cancelación física integrada; timeout
del proceso/grupo es contención local.

H2.1 integrado: el gate usa prepare_execution de Core y execute(spec) con tipos canónicos. H2 QA usa los medios y funciones oracle de JM #417,
pero modifica explícitamente checkpoints de tiempo para velocidad 1 (en
2.8 s se espera azul, no amarillo); preserva comprobación de color, audio
997Hz frente a audio de cámara, decode y perfil, sin afirmar calidad 1080p
nativa de fuentes 640x360. No se ejecuta workflow en VPS.

Requisitos scratch: Linux x86_64, Python 3.12 (runtime QA JM; Core conserva
3.11), FFmpeg/ffprobe y dependencias exactas de tests/requirements-core-fixture.txt
del pin JM. No es lock de release V2. Un job, 2 vCPU/8 GiB recomendado como
hipótesis H0, sin GPU/cloud. No benchmark ni tuning. Los repos externos
deben ser accesibles y checkout limpio; no enviar credenciales. El workflow
sube únicamente el informe sintético sin paths, no MP4 ni logs privados.

## Correcciones QA #20 / examen profundo (FER)
A: aceptar únicamente entradas Core directamente en workspace (sin symlinks, extras o paths externos), reservar engine/ sin sobrescritura; uso directo de la copia Core, hash total antes/después. No limpiar intentos. C: cámara plana H264 CFR30 MP4/MOV, origen PTS cero, sin rotación/metadata esférica, duración de stream y PTS decodificados continuos hasta extremo requerido (tolerancia de medio tick de timebase limitada a un cuarto de frame y PTS estrictamente crecientes, no pérdida de frame); master PCM WAV entero (wave), conteo de muestras físicas hasta duración requerida, sin margen de 150ms. Rechazar antes del runner. Perfil candidato exacto 30fps y número de frames, duración ±1 frame, decode completo. Validar effective_plan y manifest: no omisiones, proxy, caché, cambio de shot o master. B coordinado con #21; límites defensivos 1000 shots (Core), 200 cámaras, 6h total, extremos ≤24h y múltiplos 100ms. No reinterpretar receta.

El gate usa Core SQLite real con media, receta, claim, prepare_execution y execute; el job queda RUNNING, no simular publicación. Negativas master/cámara 100ms cortos, extra/path ajeno, workspace reutilizado; hashes de staging/originales. Fixture JM sintético, no prueba calidad nativa 1080p. No se cambia V1.

## Examen profundo de reutilización, 26/11/2026
Pines JM 1fb15f87 y Engine 9af275b. Alcance ruta Stage plana; no auditoría completa V1. Distinguir inspección estática de reproducción. Referencias relativas al pin exacto de cada repositorio.

| Ruta y líneas | Impacto y decisión |
|---|---|
| JM companion/local_video.py:80-172 | ADAPT. Project v1, resumen probe sin comprobar valid, tolerancia +150ms cámara. No importar VideoJobs, portal, auth, DB ni scheduler. Preflight C por stream/PTS/muestras, staging A y plan explícito. |
| Engine core/stages/export.py:400-485 y core/native_export_policy.py | Fallback coverage, sustitución de opcionales y negros legítimos en V1 con warning. No autorizados en V2 v0: effective plan y manifest deben corresponder a todos los shots, máster y fuentes. |
| export.py:2314-2566, 2967-3054, 4248-4268 | Proxy fallback por fallo o motion check; 360 proxy legítimo en otro perfil. V2 usa solo originales planos sin normalizados/proxy; rechaza fallback y warnings de proxy tras repair, no caché heredada. Stream-copy→encode y fallback hardware→software preservan intención y son permitidos si pasan verificaciones. Static original verificado no es fallo. |
| export.py:3262-3342, 4605-4659, 1089-1102 | apad/atrim puede completar con silencio; verify_final_audio salta fronteras fuera de fuente y captura duración errónea; no se invoca sin bookends. Quasi #20 reprodujo filtro, no un MP4 V1 defectuoso. C: PCM WAV entero con lectura real de muestras, cámara stream/PTS, frames y duración AAC del candidato, decode completo, oracle de color y tono. |
| export.py:4045-4090, 652-678 | tpad clona un frame, CFR normaliza tiempos. Frame count final no prueba cobertura fuente; PTS monotónicos, medio tick limitado a 1/4 frame y límites B. No modificar fuente para encajar. |
| export.py:760-790, 2620-2621, 3289-3291 | Incluso branding none, Stage aplica fundidos extremos de imagen/audio y color matching. video_light disabled no deshabilita matching. Defaults V1, no efectos manuales; oracle JM evita fundidos. No existe flag directo para desactivar ambos en ruta. No monkeypatch ni cambiar a Reel. Alfred/Archi deben explicitar si perfil v0 admite defaults Stage o requiere perfil neutral antes de prometer ausencia de efectos. |
| export.py:5603-5737, 5309-5317 | Medición color falla→neutral+warning; config JSON falla→{} y branding default. Config privada explícita, perfil/frame/manifest verifica estructura, no fidelidad perceptual completa. Diagnósticos privados; semántica fail-vs-warning futura publicación. |
| core/ffmpeg.py:35-118, 118-198 | Resolver prefiere ubicaciones fijas sobre PATH; cache probes por ruta/size/mtime y excepciones de cache no críticas. Verificar mismo FFmpeg/ffprobe real para preflight/render; HOME/XDG/TMP y ZUCKER_DATA_ROOT privados. Binarios/lock final Efrain. |
| core/storage.py:12-85, core/normalization.py:229-305, export.py:683-727 | Cache global, estado de proceso, worker count host/cgroup, cleanup ignore_errors. Aislamiento por intento, no caché compartida ni limpieza Core. Un job, cgroup y retención posteriores. Checkouts de confianza del operador; no sandbox contra escritor host, verificación tracked no certifica archivos ignorados. |
| export.py:5122-5127, 5196-5198 | Excepciones Reel overlay/Pillow pueden omitir contenido con log. No ruta activa Stage; no afirmar fallo V1 ni corregir fuera de alcance. |
| Dependencias transitivas ExportStage | project/storage, normalization, spherical_motion, sync/cut/edit, camera_moves, operator_avoidance, video_light, media_validation, native_export_policy, ffmpeg. Imports no equivalen a ejecución. Runtime QA Python 3.12 + NumPy/SciPy/OpenCV/Flask/Pillow; sin Mixer/Whisper/modelos. Checkout externo, no redistribuir; licencia/lock final pendientes. |

Candidatos para Alfred/JM: preflight de cobertura por stream/PTS y muestras
reales, no duración contenedor ni 150ms; postflight effective plan/manifest
para export HQ estricto, detección de proxy/omisión y decode independiente.
Aplicar solo a modo de fuentes obligatorias; preservar opcionales/retiming/VFR/360
de V1. No afirmar MP4 V1 defectuoso sin reproducción completa. No modificar V1.
Preflight de cámara decodifica fuente completa con timeout por probe 180s;
coste y RAM pendientes para grabaciones largas. 6h es límite de contrato,
no promesa operativa. No benchmark ni optimización.

## Revisión arquitectónica A y política de audio C — matiz FER/Alfred #18 (26/11/2026)

### Workspace: autoridad, no convención de carpetas

La incompatibilidad A fue introducida por nuestro guard inicial `not any(workspace.iterdir())` (PR #5 31f4aa0), no por ExportStage. Core ya crea workspace exclusivo por attempt y materializa allí un snapshot de cada Representation autorizada (`sqlite_media.py:prepare_execution`, `local_media.py:LocalMedia.read`, base 6801203). Abre medios por dir_fd/O_NOFOLLOW, copia con xb y SHA/tamaño; revalida autoridad después de staging. Los inputs de ExecutionSpec son esas copias privadas, no rutas usuario. El contrato H2.1 los define inmutables por uso y prohíbe reusar un intento; Core conserva autoridad, cleanup, fencing y publicación.

El runner fijado JM `companion/local_video.py:80-172` recibe por separado `work`, `source_files`, `master`, `progress`, `output`. Crea un Project efímero en work/edit.zuckervid, define ZUCKER_DATA_ROOT y ZUCKER_GLOBAL_CACHE dentro de work/state; file_record de Engine (`core/project.py:28-40`) registra ruta y firma, no copia ni escribe el medio. `Project.save:142` escribe project.json; export.py:400-485/737-1124/2314-2566/3262-3342 escriben artefactos, cachés, segmentos y mux privados, fuentes/master como entradas. En V1 el renderer está diseñado para recibir originales locales directamente. No exige workspace raíz vacío ni que los originales residan debajo de work.

Alternativas contrastadas:
- Vaciar workspace/recrear staging: viola autoridad Core y posibilidad de diagnóstico/recovery. Rechazada.
- Escribir todos los outputs en raíz junto a staging: colisión de nombres/estados y no reserva atómica de ejecución; obliga a Core a conocer nombres V1. Rechazada.
- Reservar engine/ como namespace de implementación (mkdir exclusivo), leer staging directo: frontera mínima. Mantiene un único snapshot por representación, sin copiar ni alterar staging; HOME/XDG/TMP y estado legado permanecen privados. El nombre engine/ no se hace contrato universal: otro adapter (Companion) tendrá su propia realización.
- Doble copia completa (decisión previa): separa daños accidentales de staged bytes, pero no añade aislamiento de privilegios (mismo UID, operador y host de confianza); añade N bytes de disco, lectura y escritura de N bytes, hash extra y doble retención. No hay escritura de fuente necesaria en ruta plana fijada; se elimina ahora, conservando SHA/tamaño pre/post y rechazo de symlink/path ajeno/extra/reuse. No hardlinks ni reflinks, no acceso a originales de usuario, no alterar modos de archivo Core. Si runner altera input, candidato no puede ser devuelto y otro intento rematerializa desde Representation canónica. No es sandbox contra proceso comprometido; separación por usuario/mount read-only/cgroup es diseño de despliegue si hace falta, no parche aquí.

Lifecycle: Core asigna/reclama intento y staging; adapter reclama su namespace y produce candidato privado; Core verifica fencing, publica y retiene/limpia cuando sea seguro; retry crea otro intento, nunca sobreescribe. Companion futuro podrá resolver representaciones locales sin enviar originales al servidor, con staging/caché bajo su política y la misma autoridad lógica. No extrapolar la copia Linux de H2 a todo Companion. No se cambia upstream, contrato Core ni V1.

### Audio: intención histórica comprobada, no atribuir intenciones privadas

- [21/07/2026 commit 0f8a06e](https://github.com/amehcgracia-byte/zucker_videos/commit/0f8a06eb6702411cd3cadb7358c0f112e100a664) "Fix continuous export audio and result flow" introduce `_mux_continuous_master_audio` y `apad,atrim`. Su padre 9753386 usaba audio por segmento y anullsrc/amix para clips logo. Objetivo observable: un máster continuo sobre montaje video-only, evitar discontinuidades de joins y garantizar duración de mux. No evidencia de que se introdujera para ocultar un máster insuficiente.
- [21/07 commit 294fbeb](https://github.com/amehcgracia-byte/zucker_videos/commit/294fbeb1ff06f6571114649c19f2569537f68b19) ajusta verificación de audio a RMS de máster y fades; [09/10 49d93b2](https://github.com/amehcgracia-byte/zucker_videos/commit/49d93b2607fbf8ca0450550464595146df9734ff) corrige falsos positivos por ataque musical y desfase decoder AAC/MP3 ~15ms sin dejar de rechazar silencio espurio. Tests fijados test_export.py:1829,2159,2187. 
- [08/10 055e7e6](https://github.com/amehcgracia-byte/zucker_videos/commit/055e7e611b17da96a091e4d844b50afefd233af2) direct concat conserva audio/vídeo decodificados; test_export_direct_concat.py compara framemd5 con content_start=.5, content_end=3.5, delay=.5. El comentario de _audio_mux_start_and_delay (export.py:2064-2069) exige no tomar audio fuera de ventana seleccionada. _mux (3262-3342) recorta contenido, retrasa, completa presentación y aplica fades. Test_export.py:1533-1551 documenta outro/L-cut acotado por End trim, nunca por longitud total; no afirmamos que esa rama esté activa en H2. Test 1764-1826 verifica sincronía marcadores y duración al normalizar frames.
- [04/11 c32dfbc](https://github.com/amehcgracia-byte/zucker_videos/commit/c32dfbcd1e492a10ad8f8ff93b67767c85973972) permite branding dentro del intervalo seleccionado. H2 branding none, master desde 0, suma de shots como intervalo de contenido íntegro, sin slots de presentación adicionales autorizados. Fundidos y color Stage siguen siendo política por aclarar con Alfred; no eliminarlos.

Decisión: **preservar apad, atrim, adelay, tpad y fades en Engine**. El fallo de C es la falta de evidencia de cobertura del contenido obligatorio antes de solicitar el mux, no el filtro. Un final silencioso que pertenece físicamente a las muestras del máster es válido; no detectar ausencia por RMS. El silencio sintético fuera del intervalo seleccionado (bookends autorizados) también es válido; H2 no dispone aún de una semántica explícita de ese intervalo. No permitir que la duración de contenedor o el relleno inventen muestras dentro de la receta. Para H2 CFR30 y PCM el límite de contenido es exacto; tolerancia de medio tick/cap 1/4 frame de PTS y ±1 frame de duración salida contemplan representación/codificación, no autorizan faltar contenido. No generalizar a retiming, VFR, 360 o L-cut sin contrato y evidencia. Si falta cobertura, error controlado, nueva revisión explícita o nuevo input, no mutar receta.

Gate pertinente: negativos ya acreditados de 100ms de falta de máster/cámara, cámara con audio largo, falso WAV. Añadir positivo de master PCM de 6s con último segundo de ceros reales (misma duración, tono previo, silencio final, original intacto), además de microgate del mux real fijado con offset 1s y bookends de 1s, 4s de contenido, 6s de salida, audio antes/después del contenido silencioso. No modificar filtro upstream. Verificar solicitud runner apunta exactamente a copias Core y no existe segunda carpeta inputs; comparar oracle positivo previo y hashes. Evidencia y estado en BRAIN #18; no medir rendimiento ni prometer compatibilidad general.
