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
A: aceptar únicamente entradas Core directamente en workspace (sin symlinks, extras o paths externos), reservar engine/ sin sobrescritura; copia adicional por protección ante legado, hash total antes/después. No limpiar intentos. C: cámara plana H264 CFR30 MP4/MOV, origen PTS cero, sin rotación/metadata esférica, duración de stream y PTS decodificados continuos hasta extremo requerido (tolerancia de un tick de timebase, no pérdida de frame); master PCM WAV, conteo de muestras físicas hasta duración requerida, sin margen de 150ms. Rechazar antes del runner. Perfil candidato exacto 30fps y número de frames, duración ±1 frame, decode completo. Validar effective_plan y manifest: no omisiones, proxy, caché, cambio de shot o master. B coordinado con #21; límites defensivos 1000 shots (Core), 200 cámaras, 6h total, extremos ≤24h y múltiplos 100ms. No reinterpretar receta.

El gate usa Core SQLite real con media, receta, claim, prepare_execution y execute; el job queda RUNNING, no simular publicación. Negativas master/cámara 100ms cortos, extra/path ajeno, workspace reutilizado; hashes de staging/originales. Fixture JM sintético, no prueba calidad nativa 1080p. No se cambia V1.
