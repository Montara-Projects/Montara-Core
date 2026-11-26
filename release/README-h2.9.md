# Release piloto H2.9 — instalación de referencia Contabo

[BRAIN #30](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/30), [contrato #28](../docs/pilot-worker-h2-8.md), [coordinación final](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/28#issuecomment-7193046490). Base de código exacta `4a7fb469d5a750e1384685695cabb24523ed78a9`, schema5. El release H2.7 sobre 36b010c y sus hashes no se reescriben. El piloto instalado de referencia es temporal: no autoriza servicio permanente ni acceso público.

## Construcción y gate

El guion `reference-h2.9.sh` utiliza la cola de pruebas autorizada (usuario no privilegiado, HOME temporal privado, MONTARA_INPUT/MONTARA_OUTPUT dedicados). Ubuntu 24.04 x86-64, git, python3+venv, apt-get/dpkg-deb, openssl y acceso a las fuentes oficiales. Requiere dos archivos históricos autorizados en input: `h2-30-historical-montara_core-0.1.0-py3-none-any.whl` y `h2-30-historical-published-v5-fixture.mp4` de H2.7. No usar datos reales; no se retuvo la DB del ensayo anterior. Se reconstruye estado v5 con el wheel histórico real y MP4 archivado mediante attestation fixture explícita, no se afirma recuperar los IDs originales. No repetir migración v4 ni render histórico.

Reutilizar los locks **completos** [Python/OIDC H2.7](manifests/h2.7-python-lock.json) y [multimedia H2.6](manifests/h2.6-ffmpeg-lock.json), verificar los 22 wheels (20 runtime/QA + 2 build) y uv, los 148 debs, ejecutables y 75 bibliotecas host por SHA256; descargar y extraer localmente, no apt install. Detener ante drift. Python 3.12.12 y pines upstream, build doble por git archive/SOURCE_DATE_EPOCH, verificar los tres recursos UI en wheel. Instalar offline en venv limpia con extra oidc; no copiar venv. Playwright 1.56.0, Chromium y bibliotecas browser son exclusivamente QA, instalados en scratch separado, no dependencias de producto. Usar fixture CA y SPKI efímero del harness aceptado sólo para ese navegador de prueba, nunca en producción.

El guion adapta el gate browser aprobado sin eliminar controles de descarga, oracle ni owner/admisión. Comprueba apertura de DB v5 anterior sin alterar filas/recibo/archivo, login del mismo actor histórico, navegador→HTTP instalado→worker CLI one-shot en unidad temporal→Engine→MP4 publicado→descarga real→reopen. Recuperación adicional sin render: proceso de fixture pierde respuesta tras preparar candidato con MP4 archivado, CLI busy y pending no mutan DB, recovery exacto/replay, interrupt sólo intento activo sin candidato y sin ejecutor, idle sin retry. La prueba de supervisor de un ciclo en unidad temporal no equivale a una unidad permanente independiente ni acredita por sí sola stop explícito del cgroup.

## Supervisor y orden operativo (propuesta para aprobación, no instalado)

Una única cuenta de servicio dedicada no root y sin login, código/pines/venv/FFmpeg de sólo lectura para servicio, DB y roots de estado privados y escribibles. Mismo TOML externo y mismo UID de HTTP/worker; secreto OIDC regular 0600 privado de ese UID, fuera del TOML y de las rutas de medios. HTTP backend loopback, worker no recibe secreto ni escucha red. No habilitar writer synthetic/admin ni otro supervisor. No borrar el lock junto a DB, no cambiar rutas/inode DB mientras haya escritor. Guardas de 2 GiB no son reserva; controlar espacio de cada FS.

Plantillas conceptuales de las unidades (rutas y UID se fijarán al autorizar el despliegue; no instalarlas copiando literalmente los placeholders):

```ini
# montara-pilot-http.service
[Service]
Type=exec
User=montara-pilot
Group=montara-pilot
WorkingDirectory=/opt/montara-pilot/current
Environment=HOME=/var/lib/montara-pilot/runtime-home
Environment=PATH=/opt/montara-pilot/current/media-tools:/usr/bin:/bin
ExecStart=/opt/montara-pilot/current/venv/bin/python -I -m montara_core --config /etc/montara-pilot/core.toml
Restart=no
KillMode=control-group
SendSIGKILL=yes
TimeoutStopSec=10s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/montara-pilot
UMask=0077
```

```ini
# montara-pilot-worker.service (manual start, no timer/enable/restart)
[Service]
Type=exec
User=montara-pilot
Group=montara-pilot
WorkingDirectory=/opt/montara-pilot/current
Environment=HOME=/var/lib/montara-pilot/runtime-home
Environment=PATH=/opt/montara-pilot/current/media-tools:/usr/bin:/bin
ExecStart=/opt/montara-pilot/current/venv/bin/python -I -m montara_core --config /etc/montara-pilot/core.toml --worker-once
Restart=no
KillMode=control-group
SendSIGKILL=yes
TimeoutStopSec=10s
RuntimeMaxSec=600s
MemoryMax=4G
CPUQuota=200%
TasksMax=256
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/montara-pilot
InaccessiblePaths=/etc/montara-pilot/client-secret
UMask=0077
```

Crear runtime-home privado y escribible por el UID de servicio; reconstruir los wrappers FFmpeg con paths definitivos, no copiar venv, binarios ni wrappers que apunten al HOME temporal. No exportar LD_LIBRARY_PATH de QA al servicio. No afirmar que estas unidades fueron instaladas/validadas. Usar start manual y comprobar salida del ciclo: 0 published/idle permite el siguiente ciclo explícito; 1–5/señal/timeout/otro exit detiene nuevos ciclos, cerrar admisión y reconciliar. No Restart=always, cron ni reintentos automáticos. Verificar límites reales, parada y espacio en el destino. Para medir parada explícita de descendientes: `pilot-stop-probe.sh`, operación root autorizada separadamente, unidad transitoria sintética (no tocar servicios reales); hijo setsid resiste TERM, se verifica cgroup común y límites, stop y escalado SIGKILL, ausencia de identidades vivas y cgroup vacío/eliminado, mismo lock libre. No requiere cambios en SentinelX, no se instala producto. No basta matar el padre. Guardar únicamente salida PASS/FAIL, no PIDs ni rutas.

Orden de mantenimiento: cerrar entrada proxy y admisión de ciclos; esperar finalización o detener toda la unidad worker; verificar cgroup vacío/eliminado y ausencia de descendientes (identidad PID/start ticks, no sólo flock); comprobar lock libre sin eliminarlo. Consultar estado durable. Recover UUID exacto tras quiescencia (sin Engine), verificar recibo y digest; si sigue activo y no recuperable decidir interrupt exacto, nunca encubrir commit ni auto-retry. Si recover falla, mantener cerrado. Sólo después parar HTTP, preparar nuevo slot y activar de forma atómica, verificar DB/recibos y volver a arrancar. Rollback de código sólo con evidencia de compatibilidad de schema y datos; no restaurar snapshot antiguo sobre escrituras. No GC de workspace activo ni artifacts.

## Primer Google/HTTPS real — procedimiento condicionado a FER

1. FER/JM acuerdan hostname exclusivo LAB V2 y método de restricción de red (VPN/ACL de origen o acceso privado aprobado). No reutilizar endpoints, clientes o túneles V1. FER autoriza los cambios concretos de DNS/TLS/proxy, unidad y cuenta de servicio; sin gasto adicional salvo aprobación. Definir un certificado válido para el hostname por un mecanismo aprobado (p. ej. ACME DNS-01 si el dominio y proveedor lo permiten); no abrir puertos para pruebas de certificado sin autorización. Mantener backend sólo loopback, limitar acceso a dos operadores/cuentas de prueba, impedir accesos directos al backend por usuarios locales no confiables. No confundir allowlist OIDC con restricción de red.
2. En Google Cloud Console, proyecto/cliente OAuth Web **independiente V2**, consentimiento en modo pruebas y registrar cuentas FER/JM como test users (si tipo External; no constituye una frontera de autorización para scope openid), redirect URI exacta `https://<hostname-lab-v2>/auth/callback`, scopes openid, sin pedir permisos adicionales. Confirmar metadata desde discovery oficial `https://accounts.google.com/.well-known/openid-configuration` y la política de verificación de dominios/consentimiento aplicable al proyecto. No cambiar ni borrar clientes V1.
3. FER introduce directamente en el servidor por canal privado el secreto del cliente (archivo regular canónico 0600 del UID de servicio, sin argv/env ni chat); client_id y sub exactos también por canal privado. Los sub se obtienen de identidad Google verificada mediante flujo OIDC autorizado y firma/issuer/aud/nonce validados (no copiar token a chat, no usar correo ni UUID interno). Si aún no se conocen, preparar un bootstrap de identidad local y aislado, separado del piloto, que sólo muestre el sub verificado al operador; no abrir temporalmente la allowlist. No provisionar ni auto-link de cuentas antiguas.
4. Crear estado privado y slots versionados a partir del wheel exacto/locks ya verificados. Rehacer venv/FFmpeg local en destino, verificar SHA, config, pines, permisos, espacio y recursos UI. TOML: [http] puerto loopback de servicio; [storage] DB/roots absolutos separados; [oidc] issuer https://accounts.google.com, authorization_endpoint/token_endpoint/jwks_uri exactos de discovery, client_id, client_secret_file, public_origin=https://hostname; [pilot] allowed_subjects 1–4 sub exactos, renderer_root/engine_root absolutos separados, render_timeout_sec=180. Config externa, no secret dentro TOML. Validar con --check-config, que no lee secreto ni hace bind; arranque real verifica secreto.
5. Instalar/validar proxy TLS restringido con Host y Origin exactos, preservar ruta/query callback, sin reescribir identidad, sin cache ni logs de query/cookies/codes/tokens, límites de tasa/conexión/cuerpo/timeout y certificado válido. Proxy no debe exponer rutas de archivos ni endpoint worker. No usar CA/SPKI del QA. Config específica de proxy depende de elección/hostname de FER y debe aprobarse antes de aplicarla. Arrancar HTTP y probar health/restricción antes de abrir a las dos cuentas. No habilitar acceso general.
6. Dos perfiles navegador, login Google real, UUID distintos y relogin estable, no auto-link. Crear proyecto de prueba, operador coloca tres fixtures sintéticos, registrar, guardar receta, solicitar job; iniciar worker one-shot, comprobar estado y descarga SHA/size, otro owner y anónimo rechazados, logout y restart revocan sesión sin perder datos. Un render material, no cuatro. Cerrar acceso al finalizar. Google real/HTTPS real/stop dedicado no acreditados hasta ejecutar y conservar evidencia. No editor completo, originales de usuarios ni producción.

**Intervenciones FER imprescindibles:** autorización del probe transitorio (puede ejecutar el guion exacto como root), elegir/autorizar hostname y red/TLS, crear cliente Google aislado/consentimiento y facilitar datos de configuración por canal privado; una intervención root agrupada para crear cuenta, directorios, unidades y proxy tras definir esos valores, y validación humana con JM. No ampliar permisos genéricos de SentinelX ni usar túnel para saltarse controles. El probe se puede ejecutar antes de decisiones Google. El resto de preparación/reconstrucción/pruebas se realiza con autorización existente.

## Métricas y límites

Wall monotónico, CPU user/system y RSS máximo de hijo recolectado (no RSS agregado instantáneo), memoria cgroup muestreada cada 200ms y pico global incluye cache/browser/instalación; bytes lógicos de state y delta FS no I/O físico. No benchmark largo ni prueba de CPUQuota propia bajo unidad temporal. Supervisor separado, stop real, piloto externo y DR físico se declaran según evidencia efectiva, no por plantillas.

### Referencias oficiales de Google

- [OpenID Connect y Discovery](https://developers.google.com/identity/openid-connect/openid-connect).
- [OAuth para aplicaciones web: redirect URI exacta](https://developers.google.com/identity/protocols/oauth2/web-server).
- [Políticas OAuth, dominios autorizados y HTTPS](https://developers.google.com/identity/protocols/oauth2/policies).

Se utilizará un dominio propio/autorizado; no asumir que un hostname temporal ajeno es elegible ni usar URI no HTTPS para el piloto real.

### Incidencia QA y reejecución focalizada

Primer intento: 100/100 suite instalada y apertura histórica v5 PASS; Chromium falló antes de render en la comprobación de bibliotecas de Playwright. Se conservan [métricas del primer intento](manifests/h2.9-first-attempt-metrics.json). Ajuste del harness: pasar LD_LIBRARY_PATH de las bibliotecas locales al proceso de Playwright (además del navegador) y verificar ldd sin dependencias ausentes; sin deshabilitar comprobaciones ni instalar globalmente. Fetch superficial por SHA exacto para evitar historial innecesario, conservando pin histórico. Para una reproducción nueva, ejecutar sin H2_REUSE_SUITE; la reejecución focalizada del mismo gate permite H2_REUSE_SUITE=1 sólo tras PASS de la suite sobre misma fuente y locks, no equivale a una suite nueva ni a prueba de bytes de wheel del primer intento (no retenido).

## Evidencia Contabo 26/11/2026 — instalación y recuperación PASS; stop dedicado PENDIENTE

[Build](manifests/h2.9-build.json), [manifest](manifests/h2.9.json), [calidad](manifests/h2.9-quality.json), [recovery](manifests/h2.9-recovery.json), [métricas](manifests/h2.9-metrics.json), [worker](manifests/h2.9-worker-metrics.json), [QA wheels](manifests/h2.9-qa-wheel-lock.json), [QA debs](manifests/h2.9-qa-browser-lock.json). Primer intento 100/100 (26.151s) con locks exactos y apertura histórica PASS; Chromium preflight falló antes de render. Segundo intento con bibliotecas locales visibles al proceso Playwright y ldd PASS, sin deshabilitar validación, sin repetir suite; JOB_EXIT=0. Aviso de dependencias del instalador Playwright previo a extracción local, lanzamiento y descarga posteriores PASS. Dos builds finales binarios iguales, 50 archivos, SHA256 c7e11118d2d1c5bffe658b3ed2ef11ed8101e47e6ef027bd6a601c442bb8304f. Histórico v5 reconstruido igual SHA #27; 22 ruedas Python y uv, 148 debs/75 host libs, locks idénticos. Playwright 1.56.0 / Chromium 141.0.7390.37, build1194, QA separado.

13 tablas/21 filas históricas intactas, mismo inode DB, recibo y bytes de MP4 archivado; identidad histórica fixture relogin misma cuenta. Un render real nuevo desde Chromium HTTPS IdP fixture, UI/HTTP instalada y supervisor one-shot en cgroup temporal existente: admisión, dos usuarios, owner/anon negativos, publicación, descarga browser real y digest, reopen/logout/relogin, idle, 5/5 colores, 3/3 audio, decode PASS; 6s 1080p30 279812 B SHA256 e54dad8764762ef56c0e362dc27ac5f280fb29713a3f87bcf7647c5979e4830f. No afirmar determinismo universal ni calidad de fuente nativa 1080p. No render histórico duplicado. CLI busy/pending y stale sin mutación, proceso fixture exit23 con candidato archivado, recover exacto y replay sin render, interrupt exacto activo sin candidato tras quiescencia, idle sin retry, lock inode intacto, recibos histórico y real preservados, schema5 e integrity/FK. No rollback entre releases distintos acreditado.

Gate browser 59.161s; ciclo worker 33.535s, CPU 68.615237s (~2.05 cores medios), max RSS hijo recolectado 449624 KiB (~439 MiB), no RSS agregado árbol. Recovery 3.964s; estado lógico final 6413301B, slot 426049037B, browser QA629862420B, disco libre97079844864B antes cleanup. **Cgroup global peak 4294967296B = límite 4 GiB**, muestreo durante browser casi límite, incluye caché de descargas/build, dos slots y Chromium; no margen acreditado ni OOM events medidos; gate exit0. No atribuir pico a worker ni extrapolar, separar QA/HTTP/worker y verificar memoria.events y margen en unidad dedicada antes de piloto; mantener un job. CPUQuota propia NOT RUN. No aumentar VPS automáticamente.

Wheel y MP4 descargado, evidencia e informes archivados privadamente; cola de pruebas idle y output vacío, sin cambios admin/gasto/credenciales/servicios. **STOP explícito y extinción de descendientes setsid resistentes TERM NOT RUN**, SentinelX sólo permite start de unidad temporal, no stop ni crear unidades. No afirmar limpieza cgroup dura ni servicio dedicado instalado. Probe transitorio root preparado y copiado de blob Git 4a44476c68f5c6af4c7f23d96201eea409b4de83; operador autorizado puede copiar a archivo root privado, verificar hash Git y ejecutar. No modificar política agente. Resultado PASS/FAIL debe añadirse antes de aceptar supervisión completa. Google/HTTPS real NOT RUN, autorización FER/hostname/red/cliente/sub/secreto privados y despliegue persistente pendientes. Dictamen GO de revisión de paquete y recuperación, condicionado para H2.9 completo a prueba explícita de parada y margen operativo.

## Continuación #30 — parada acreditada y margen dedicado (26/11/2026)

[Instrucción Alfred y evidencia humana](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/30#issuecomment-7194405249): FER ejecutó el guion transitorio verificado del blob `4a44476c68f5c6af4c7f23d96201eea409b4de83` y comunicó los seis PASS: SCRIPT_INTEGRITY, SEPARATE_SESSION_AND_CGROUP, LIMITS, CGROUP_EMPTY_AND_IDENTITIES_GONE, FLOCK_FREE_SAME_INODE, STOP_PROBE. Se acepta como evidencia humana del cgroup sintético con hijo setsid resistente a TERM, límites CPU/RAM/tasks, extinción efectiva y flock del mismo inode. No repetir. No equivale a una unidad permanente instalada.

[Probe de margen](pilot-margin-probe.sh) preparado y verificado en workspace autorizado, **pendiente ejecución root por FER**, porque SentinelX sólo permite iniciar la unidad fija y no crear cgroups independientes. Root orquesta dos unidades transitorias únicas, no instala unidades permanentes ni cambia permisos del agente. Copia y verifica SHA del wheel archivado H2.9, preparación no privilegiada con los locks históricos exactos y fuentes fijadas, crea un fixture nuevo sintético y una única exportación queued. Tras terminar preparación, unidad worker nueva (sin HTTP, Chromium, instalación ni build dentro de su cgroup), CPUQuota=200%, MemoryMax=4GiB, TasksMax=256, RuntimeMaxSec=600, KillMode=control-group, Restart=no. Ejecuta **un único render adicional necesario** para medir el worker bajo esos límites; no repite suite, migración ni matriz browser. Lee memory.peak, memory.events antes/después, cpu.stat y límites efectivos antes de que systemd recoja el cgroup; verifica recibo/hash/DB tras capturar métricas. Criterio operativo provisional conservador para este fixture: al menos 20% de margen respecto a 4GiB, cero incremento de max/oom/oom_kill/oom_group_kill; publicar también eventos high/low y CPU throttling sin confundirlos con fallos. No extrapolar capacidad ni exigir ampliar VPS por una muestra. El script limpia sólo su scratch temporal y no toca datos/artefactos históricos. Si falla, no reintentar render automáticamente.

### Acceso real: estado y decisiones de FER

- **Ningún usuario real V2 confirmado**: todavía no hay despliegue Google del piloto ni configuración productiva de allowed_subjects. owner-a/owner-b eran identidades sintéticas, no emails ni cuentas FER/JM. No inferir usuarios a partir de permisos GitHub, SSH, Cloud Console o V1. Propuesta de dos operadores FER y JM, confirmar en privado. La lista de test users en Google no es comprobable con el acceso autorizado actual.
- Google Auth Platform: proyecto OAuth separado V2 (recomendado para no modificar branding/audience/consentimiento de V1), cliente Web independiente. Branding a nivel del **proyecto**, no por cliente: nombre Montara (o Montara V2 · Piloto, sujeto a acuerdo), logo oficial, correo soporte/contacto controlado, homepage/privacidad/términos reales y dominios autorizados de propiedad o uso autorizado. No inventar páginas legales, logo ni dominio. La publicación de nombre/logo puede requerir verificación de marca; no garantizar que aparezca mientras esté en pruebas. Audience External/Testing si FER/JM usan cuentas ajenas a una misma organización Workspace, test users privados. Data Access sólo openid (implementación actual); no pedir email/profile/Drive innecesariamente.
- **Importante**: [Google OAuth app state](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview) documenta excepción a test-user allowlist para scopes básicos openid/email/profile. No confiar en Testing como restricción de acceso. Mantener restricción de red + allowlist interna de issuer/sub exactos de Core, antes de provisionar cuenta. FER/JM confirman cuentas vía canal privado; jamás publicar emails/sub.
- URL propuesta `https://v2.<dominio-controlado>` o `https://lab-v2.<dominio-controlado>`; placeholders, no URL activa. FER confirma dominio que controla y método red (preferir acceso privado o ACL de origen aprobada), TLS válido y callback exacto `https://<hostname>/auth/callback`. Config public_origin sin barra final; backend loopback, DNS/proxy/cert independientes, no modificación V1. Cliente OAuth Web y callback sólo después de acordar hostname; usar discovery Google y mantener issuer exacto verificado.
- Obtención de sub: no solicitar ID tokens ni copiar secretos a chat. Enrolamiento privado de operador, usando flujo actual authorization code/PKCE/nonce y verificación criptográfica de OIDCProvider, sin provisionar cuentas ni abrir allowlist. Si hace falta una herramienta de enrolamiento, diseñar y autorizar como operación separada, aislada, temporal y sin acceso público, no nuevo método de autenticación ni cambio de Core en H2.9. Verificar identidad de FER/JM por canal privado, recoger sólo issuer/sub y guardar allowlist en TOML privado. El piloto real no se inicia hasta tener al menos un sub verificado y una restricción efectiva.
- FER autoriza posteriormente instalación permanente / DNS / TLS, coloca secreto directamente en servidor, confirma logo/URLs legales y cuentas; no crear credenciales ahora. Futuro email/contraseña y vinculación explícita de identidades es propuesta de producto pendiente FER/JM, fuera de H2.9; no auto-link por correo.

Referencias oficiales: [Branding](https://support.google.com/cloud/answer/15549049), [verificación de marca](https://developers.google.com/identity/verification/authentication-verification), [políticas OAuth](https://developers.google.com/identity/protocols/oauth2/policies), [OIDC](https://developers.google.com/identity/openid-connect/openid-connect). Estado: stop transitorio PASS (humano); margen worker dedicado pendiente; Google/TLS real y servicio permanente NOT RUN.
