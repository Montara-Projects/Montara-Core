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

No afirmar que estas unidades fueron instaladas/validadas. Usar start manual y comprobar salida del ciclo: 0 published/idle permite el siguiente ciclo explícito; 1–5/señal/timeout/otro exit detiene nuevos ciclos, cerrar admisión y reconciliar. No Restart=always, cron ni reintentos automáticos. Verificar límites reales, parada y espacio en el destino. Para medir parada explícita de descendientes: `pilot-stop-probe.sh`, operación root autorizada separadamente, unidad transitoria sintética (no tocar servicios reales); hijo setsid resiste TERM, se verifica cgroup común y límites, stop y escalado SIGKILL, ausencia de identidades vivas y cgroup vacío/eliminado, mismo lock libre. No requiere cambios en SentinelX, no se instala producto. No basta matar el padre. Guardar únicamente salida PASS/FAIL, no PIDs ni rutas.

Orden de mantenimiento: cerrar entrada proxy y admisión de ciclos; esperar finalización o detener toda la unidad worker; verificar cgroup vacío/eliminado y ausencia de descendientes (identidad PID/start ticks, no sólo flock); comprobar lock libre sin eliminarlo. Consultar estado durable. Recover UUID exacto tras quiescencia (sin Engine), verificar recibo y digest; si sigue activo y no recuperable decidir interrupt exacto, nunca encubrir commit ni auto-retry. Si recover falla, mantener cerrado. Sólo después parar HTTP, preparar nuevo slot y activar de forma atómica, verificar DB/recibos y volver a arrancar. Rollback de código sólo con evidencia de compatibilidad de schema y datos; no restaurar snapshot antiguo sobre escrituras. No GC de workspace activo ni artifacts.

## Primer Google/HTTPS real — procedimiento condicionado a FER

1. FER/JM acuerdan hostname exclusivo LAB V2 y método de restricción de red (VPN/ACL de origen o acceso privado aprobado). No reutilizar endpoints, clientes o túneles V1. FER autoriza los cambios concretos de DNS/TLS/proxy, unidad y cuenta de servicio; sin gasto adicional salvo aprobación. Definir un certificado válido para el hostname por un mecanismo aprobado (p. ej. ACME DNS-01 si el dominio y proveedor lo permiten); no abrir puertos para pruebas de certificado sin autorización. Mantener backend sólo loopback, limitar acceso a dos operadores/cuentas de prueba, impedir accesos directos al backend por usuarios locales no confiables. No confundir allowlist OIDC con restricción de red.
2. En Google Cloud Console, proyecto/cliente OAuth Web **independiente V2**, consentimiento en modo pruebas y cuentas FER/JM admitidas (si tipo External), redirect URI exacta `https://<hostname-lab-v2>/auth/callback`, scopes openid, sin pedir permisos adicionales. Confirmar metadata desde discovery oficial `https://accounts.google.com/.well-known/openid-configuration` y la política de verificación de dominios/consentimiento aplicable al proyecto. No cambiar ni borrar clientes V1.
3. FER introduce directamente en el servidor por canal privado el secreto del cliente (archivo regular canónico 0600 del UID de servicio, sin argv/env ni chat); client_id y sub exactos también por canal privado. Los sub se obtienen de identidad Google verificada mediante flujo OIDC autorizado y firma/issuer/aud/nonce validados (no copiar token a chat, no usar correo ni UUID interno). Si aún no se conocen, preparar un bootstrap de identidad local y aislado, separado del piloto, que sólo muestre el sub verificado al operador; no abrir temporalmente la allowlist. No provisionar ni auto-link de cuentas antiguas.
4. Crear estado privado y slots versionados a partir del wheel exacto/locks ya verificados. Rehacer venv/FFmpeg local en destino, verificar SHA, config, pines, permisos, espacio y recursos UI. TOML: [http] puerto loopback de servicio; [storage] DB/roots absolutos separados; [oidc] issuer https://accounts.google.com, authorization_endpoint/token_endpoint/jwks_uri exactos de discovery, client_id, client_secret_file, public_origin=https://hostname; [pilot] allowed_subjects 1–4 sub exactos, renderer_root/engine_root absolutos separados, render_timeout_sec=180. Config externa, no secret dentro TOML. Validar con --check-config, que no lee secreto ni hace bind; arranque real verifica secreto.
5. Instalar/validar proxy TLS restringido con Host y Origin exactos, preservar ruta/query callback, sin reescribir identidad, sin cache ni logs de query/cookies/codes/tokens, límites de tasa/conexión/cuerpo/timeout y certificado válido. Proxy no debe exponer rutas de archivos ni endpoint worker. No usar CA/SPKI del QA. Config específica de proxy depende de elección/hostname de FER y debe aprobarse antes de aplicarla. Arrancar HTTP y probar health/restricción antes de abrir a las dos cuentas. No habilitar acceso general.
6. Dos perfiles navegador, login Google real, UUID distintos y relogin estable, no auto-link. Crear proyecto de prueba, operador coloca tres fixtures sintéticos, registrar, guardar receta, solicitar job; iniciar worker one-shot, comprobar estado y descarga SHA/size, otro owner y anónimo rechazados, logout y restart revocan sesión sin perder datos. Un render material, no cuatro. Cerrar acceso al finalizar. Google real/HTTPS real/stop dedicado no acreditados hasta ejecutar y conservar evidencia. No editor completo, originales de usuarios ni producción.

**Intervenciones FER imprescindibles:** autorización del probe transitorio (puede ejecutar el guion exacto como root), elegir/autorizar hostname y red/TLS, crear cliente Google aislado/consentimiento y facilitar datos de configuración por canal privado; una intervención root agrupada para crear cuenta, directorios, unidades y proxy tras definir esos valores, y validación humana con JM. No ampliar permisos genéricos de SentinelX ni usar túnel para saltarse controles. El probe se puede ejecutar antes de decisiones Google. El resto de preparación/reconstrucción/pruebas se realiza con autorización existente.

## Métricas y límites

Wall monotónico, CPU user/system y RSS máximo de hijo recolectado (no RSS agregado instantáneo), memoria cgroup muestreada cada 200ms y pico global incluye cache/browser/instalación; bytes lógicos de state y delta FS no I/O físico. No benchmark largo ni prueba de CPUQuota propia bajo unidad temporal. Supervisor separado, stop real, piloto externo y DR físico se declaran según evidencia efectiva, no por plantillas.
