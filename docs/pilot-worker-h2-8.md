# H2.8 — piloto de ingeniería y worker instalado

[BRAIN #28](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/28) / [ADR #415](https://github.com/Montara-Projects/Montara/issues/415#issuecomment-7141340666). Un slice Linux mononodo: identidad OIDC → proyecto → medios fixture → receta → Job → worker → Engine → publicación → descarga. No editor definitivo, upload, nuevos proveedores, distribución ni semántica Stage nueva. Schema5 intacto; release/ histórico v4/v5 se conserva, Efrain único escritor release/. 

## Configuración y admisión

Mismo TOML externo y roots privadas de H2.5. Agregar [pilot] con `allowed_subjects` (1–4 sub exactos del issuer configurado, obtenidos por canal privado de operador, jamás email o UUID interno), `renderer_root` y `engine_root` absolutos canónicos existentes y separados de estado mutable y entre sí, `render_timeout_sec` entero 30–300 (default 180). DB canónica. No secretos ni subjects reales en repo. Startup OIDC con [pilot] exige allowlist y sólo provisiona tras comprobación criptográfica issuer/sub; cambios de allowlist requieren reiniciar HTTP (sesiones revocadas). Sin [pilot] conserva H2.5 health/HTTP de ingeniería, no UI/worker nuevo; **no usar esa configuración para piloto accesible a terceros**. Sin [oidc] health-only.

Límites de este piloto (no cambiar el dominio general): 4 proyectos por owner, 8 jobs históricos totales por owner, 1 activo queued/running por owner, 8 revisiones de receta/proyecto, tres locators de fixture exactos camera-a.mp4, camera-b.mp4, master.wav por proyecto, registro repetido idéntico no duplica representación, modificación detectada exige nueva preparación autorizada. Máximo 256 MiB/fichero durante lectura, 512 MiB de inputs únicos/job, 30 shots, endpoints y duración total ≤60s. Guardas en Core, conteos y creación bajo transacción SQLite, replay de clave exacta anterior al límite, snapshot revalidado en staging; límites no se aplican retrospectivamente a recetas históricas. No garantías de throughput ni de ocupación real pico. 30 inicios login/min global y 30 mutaciones válidas/min por owner (memoria del proceso, sin logout bloqueado); límites H2.5 de sesiones y body siguen. Sin upload ni almacenamiento de datos reales. Sólo operador escribe archivos en carpeta UUID del proyecto; revisar tamaño/duración fixture y permisos antes de colocar. No habilitar otros writers/admin/synthetic sobre misma DB durante piloto. Trabajo previo no admitido no se reclama; conservar históricos y operador decide su gestión. No auto-link por correo.

## Interfaz

Abrir / en navegador: login OIDC, seleccionar/crear proyecto, mostrar UUID para que operador coloque los tres medios sintéticos, registrar fixture, guardar montaje de 6s A(0–2s)/B(2–4s)/A(4–6s), máster completo desde cero, Stage no neutral (fundidos/color matching, sin branding ni bookends extra). Solicitar render con clave de idempotencia (conservada ante respuesta ambigua en la página, nunca reenvío automático al recargar), progreso mediante estado persistido queued/running/succeeded/failed/cancelled (no porcentaje ficticio), descargar sólo si publicado. Reload/relogin seleccionar proyecto y recuperar resumen/recibo. GET /api/projects/{id}/pilot owner-scoped devuelve media con nombres fijos, última receta y últimos jobs, sin paths locales. Sólo recursos empaquetados exactos /, /pilot.js, /pilot.css, CSP self, sin HTML de usuario ni innerHTML ni scripts remotos ni almacenamiento navegador. Usa API y cookie existentes; logout POST Origin y JSON, redirect /; no worker API, no rutas estáticas de medios/resultados.

## CLI worker de confianza (Linux)

Mismo intérprete instalado aislado (Python3.12 target Efrain), mismo wheel y [pilot] TOML; extra OIDC y closure Engine/FFmpeg/ffprobe de release aprobados. Worker no lee secreto OIDC, no abre listener, no recibe actor/job desde cliente; Core reclama export elegible y owner en una transacción, utiliza AttemptRef exacto. Claim global serial y lock flock privado junto a DB (no eliminar lock, no cambiar ruta/inode DB mientras activo); no ejecuta synthetic ni jobs sin snapshot o no admitidos. Reserva libre mínima 2 GiB en FS DB/media/workspace/artifact antes de claim, **no reserva dura ni cálculo de pico**. Mantener raíces privadas, monitorizar espacio y aplicar cuota FS/operador; no correr múltiples workers ni escribir estado desde V1.

```sh
python -I -m montara_core --config /path/to/core.toml --check-config
python -I -m montara_core --config /path/to/core.toml
# En unidad separada bajo control de operador/supervisor único:
python -I -m montara_core --config /path/to/core.toml --worker-once
# Sólo tras confirmar quiescencia del ejecutor y sus descendientes:
python -I -m montara_core --config /path/to/core.toml --worker-recover ATTEMPT_UUID
python -I -m montara_core --config /path/to/core.toml --worker-interrupt ATTEMPT_UUID
```

Un `--worker-once` ejecuta como máximo un export: prepara snapshot, verifica/ejecuta adapter y publica por Core. Fallo conocido prepublicación marca sólo ese intento failed. Error de publicación/inesperado deja estado intacto para reconciliar (puede haber commit); no auto-retry, no rerender ni cancel automático. `--worker-recover` reconcilia candidato/intent/final/recibo exacto activo o confirmado, sin Engine; `--worker-interrupt` marca un intento activo exacto interrupted→failed, no borra workspace. No recuperar/interrupt mientras ejecutor siga activo; flock sólo serializa procesos CLI, no prueba que descendientes de un proceso abruptamente muerto hayan terminado. Nunca usar interrupt para encubrir un commit o resolver un fallo de calidad. Nueva solicitud explícita tras fallo, no retry automático. 

| Exit | Significado |
|---|---|
| 0 | published / recovered / interrupted / idle (stdout JSON con estado y, si corresponde, IDs exactos) |
| 1 | error infraestructura o disco insuficiente; inspeccionar estado antes de nueva ejecución |
| 2 | configuración/modo/UUID inválido |
| 3 | worker busy, intento pendiente o no exacto; no ejecutar ni mutar otro intento |
| 4 | fallo prepublicación controlado, Job failed |
| 5 | recuperación requerida; revisar recibo/estado, no reiniciar a ciegas |

Orden operador: validar config/pines/PATH/roots/permisos/capacidad, arrancar HTTP loopback bajo TLS proxy restringido, usuarios admitidos login y preparación, iniciar worker one-shot al haber job queued. Supervisor externo opcional a cadencia corta sólo para exit0; si 1–5 detener admisión de nuevos ciclos e intervenir/reconciliar. Sin daemon scheduler dentro de Core. En Linux unidad aislada con servicio UID no root, KillMode=control-group (incluye runner/FFmpeg en nueva sesión), TimeoutStopSec acotado, RuntimeMaxSec según fixture y pre/postflight (p.ej. 10 min), MemoryMax=4GiB, CPUQuota=200%, TasksMax=256 inicialmente, sin restart automático tras error, ruta runtime y PATH de release verificados. No modificar servicios por este documento; Efrain define unidad en siguiente release autorizado. Parada: cerrar entrada proxy/admisión y supervisor de nuevos jobs; esperar worker termine o detener unidad completa, comprobar cgroup vacío/lock libre, reconciliar intento exacto, sólo después detener/reiniciar HTTP o cambiar release. Nunca limpiar workspace activo, ni artifact_root al limpiar scratch. Guardas no certifican DR físico, sandbox host, cuotas completas ni scheduler distribuido.

## Gate reproducible

CI desde wheel instalado fuera checkout: suite previa más tests piloto (política/admisión/claims/recovery, fixture bytes no AV); gate Chromium con IdP HTTPS fixture de pruebas y proxy TLS local de pruebas, backend CLI real, dos sesiones, identidad no admitida rechazada, crear proyecto A, fixture colocado por operador, UI registra y guarda receta, solicita job, worker CLI separado renderiza una vez con Engine fijado, publicación H2.4 y descarga browser, SHA/tamaño/decodificación/oracle 5 colores y 3 ventanas máster, B/anon rechazados, recarga/logout/relogin y recuperación de resultado, idle sin duplicar. Sólo informe saneado; no guardar secretos/MP4 en artefactos CI. El script histórico engine_h2_smoke.py y evidencia A/B/C no se eliminan; gate focalizado anterior disponible por workflow_dispatch explícito, PR usa gate combinado en vez de duplicar renders. Browser y IdP/CA/proxy fixture son QA-only, no runtime deps.

## Google/TLS real y release futuro

[Coordinación Efrain #28](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/28#issuecomment-7187867900), [#27](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/27#issuecomment-7187901540). #27 release v5 histórico sobre 36b010c, no incluye este worker/UI. Próximo paquete tras integración debe verificar mismos pins/closure target y que los tres recursos UI están dentro del wheel, CLI y gate browser; preservar estado/recibos, no alterar locks históricos ni restaurar DB anterior. 

FER debe autorizar cliente Web Google independiente de V1, issuer exacto, redirect HTTPS /auth/callback, acceso limitado a cuentas de prueba y sub explícitos verificados por operador, secreto externo regular canónico owner servicio 0600; no publicar sub/client_secret/logs de callback. Proxy TLS exclusivo LAB: acceso de red restringido (VPN/allowlist/acceso privado), preservar Host, Origin y query callback, sin cache ni logs tokens/codes/cookies, rate/connection/body/timeouts y límites de recursos, bloquear acceso directo al loopback desde otros usuarios no confiables. No confiar en X-Forwarded-* para identidad. Gate final con dos navegadores y Google real, fixture autorizado, worker instalado y descarga íntegra; NOT RUN hasta autorización/credenciales y despliegue aislado. No exponer Internet general ni usar medios reales; admisión/retención/DR/seguridad edge y decisión perfil producto pendientes.
