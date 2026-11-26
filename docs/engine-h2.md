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
el adapter copia y verifica SHA256 completo, evita sobrescribir intentos,
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
A: aceptar únicamente entradas Core directamente en workspace (sin symlinks, extras o paths externos), reservar engine/ sin sobrescritura; copia adicional por protección ante legado, hash total antes/después. No limpiar intentos. C: cámara plana H264 CFR30 MP4/MOV, origen PTS cero, sin rotación/metadata esférica, duración de stream y PTS decodificados continuos hasta extremo requerido (tolerancia de medio tick de timebase limitada a un cuarto de frame y PTS estrictamente crecientes, no pérdida de frame); master PCM WAV entero (wave), conteo de muestras físicas hasta duración requerida, sin margen de 150ms. Rechazar antes del runner. Perfil candidato exacto 30fps y número de frames, duración ±1 frame, decode completo. Validar effective_plan y manifest: no omisiones, proxy, caché, cambio de shot o master. B coordinado con #21; límites defensivos 1000 shots (Core), 200 cámaras, 6h total, extremos ≤24h y múltiplos 100ms. No reinterpretar receta.

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
