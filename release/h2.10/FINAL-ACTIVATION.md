# H2.10 — Activación final, sin reinstalación ni enrolamiento

[Incidencia Alfred-002 #32](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/32#issuecomment-7229009039), main e5223745de8dbd33142f53e0570872585e681591. PR19 integrada. FER acredita runtime instalado/sellado, ACL y HTTPS/QUIC reales durante enrolamiento, dos VERIFIED_SUBJECT_ACCEPTED, PRIVATE_CONFIG_PREPARED, Configuration valid e INITIAL_TWO_SUBJECTS_AND_ENROLMENT_REMOVED PASS. Tras ACTIVAR_CORE: PILOT_STOPPED_REVIEW_REQUIRED, sin PILOTO_LISTO. Alfred confirma túnel down/0, Access protegido. HTTP Result=success, ExecMainCode=0, ExecMainStatus=0, inactive/dead y journal vacío. Esto no prueba que el proceso arrancase ni identifica causa retrospectiva.

## Diagnóstico acotado

El tail exacto de PR19, idéntico a PR18/17, tras el último marcador ejecuta confirm (read -r y test literal), nft list table, systemctl start HTTP, espera/health/bind/ACL, start ingress y confirm PILOTO_LISTO. No tiene marcador por cada instrucción ni ERR con línea. El trap EXIT sólo informa de parada. Con journal HTTP vacío, plausible fallo antes de ExecStart (lectura/confirmación literal o ACL o transacción systemd). No afirmar cuál sin evidencia. No hay base para cambiar Conflicts de HTTP: el enrolador temporal se retiró intencionalmente, Conflicts no equivale a Requires; no recrearlo. No se ha podido leer estado real por SentinelX (service_status unsupported_op y slot restringido); no ampliar permisos ni bypass.

Otra comprobación material: --check-config sólo parsea el TOML, no lee el secreto ni construye OIDCProvider/SQLite. Antes de arrancar, comprobar lectura efectiva del secreto y constructor local OIDC (sin red) bajo UID real, acceso roots y coherencia de admisión. El primer arranque puede crear DB schema5; las dos identidades verificadas ya están en configuración privada, sus cuentas internas se provisionan al login, no por enrolamiento. No generar UUID ni crear proyectos artificiales. DB ausente esperada; si existe tras intento, sólo aceptar schema5 íntegro vacío sin sidecars, consulta mode=ro&immutable=1; cualquier otro estado conservar y revisar, no eliminar.

## Procedimiento final único

[finish-activation.sh](finish-activation.sh), blob Git 171f504ccae92c3579a8dd471f5e5b31b116450c, staged y readback exactos. Único script independiente de instaladores anteriores, sin bundles adicionales. Modos inspect (sólo lectura, no inicio servicios) y activate (misma inspección, pausa explícita ACTIVAR_CORE, dos arranques solamente). No cambia archivos de configuración ni unidades, no reaplica firewall, no lee/imprime valores privados, no apt/pip/Engine/renderer/wheel/instalación/enrolamiento/render/worker ni enable. El loader copia como lector sentinelx autorizado a temporal root privado, verifica hash antes de ejecutar. Estado sólo se modifica por el arranque normal de Core (DB nueva) y conector, autorizados. No tocar Access/Worker Cloudflare/V1.

Preflight comprueba estados y timestamps systemd, conteo journal del boot (no mensajes), ausencia del enrolador y sus archivos, ausencia de procesos de UIDs y jobs systemd piloto, units exactas por hashes y drop-in ACL exacto, loaded/no daemon reload/static, systemd-analyze verify, ACL activa y contenido efectivo nft JSON cotejado con reglas esperadas de ambos puertos (admite representación TCP explícita equivalente), listeners ausentes. Verifica current/slot sellado, propietarios y permisos, wheel/conector originales, generador y plantilla aprobados; deriva config en memoria desde operator.toml con plantilla exacta salvo origen, exige 2 sujetos distintos y igualdad TOML con core.toml, sin mostrar valores. Huella privada de archivos de configuración, sujetos y secretos sólo en memoria, compara preflight/final. Lee secreto con función Core y constructor OIDC bajo UID servicio, comprueba import desde slot, acceso raíces y separación token/credenciales; no callbacks. Sólo permite DB inicial ausente o schema5 vacío íntegro y sin sidecars, no modifica DB para inspeccionar.

Tras autorización: start HTTP, readiness JSON status ok y loopback exclusivo, ACL positiva desde UID ingress y negativas nobody/pilot/enrol, DB existente; start ingress, Alfred confirma QUIC/bindings/Access/health protegida y negativas sin login Core hasta cerrar gate. PILOTO_LISTO explícito, revalida units activas/ACL efectiva/loopback/worker parado/enrolador ausente y huella privada intacta. Marker PILOT_INSTALLED_PENDING_HUMAN_QA=PASS. No worker automático. Las comprobaciones externas corresponden a Alfred, no se infieren por proceso activo. Si cualquier fallo, marcadores ACTIVATION_FAILED_PHASE y ACTIVATION_ERROR_LINE cuando ERR (sin BASH_COMMAND), RC, estados y parada de HTTP/ingress iniciados sólo por este bloque; comprueba MainPID/estado/cgroup anterior/listeners, si parada no verificada Alfred fail-close edge; no borrar config/subjects/DB ni retry automático. Si falla preflight, no arranca ni para nada. No garantizar trap ante SIGKILL/caída.

**FER: ejecutar sólo tras coordinación Alfred.** Modo inspect permite devolver diagnóstico sin iniciar; para evitar dos sesiones se puede usar mode=activate tras revisión y preflight externo de Alfred, pues contiene idéntica inspección y pausa ACTIVAR_CORE. Si cualquier divergencia, parar, devolver fase/line/RC y estados (nunca valores privados). No relanzar instalador. Para activar, utilizar este mismo bloque con mode=activate en lugar de inspect, exclusivamente tras autorización de Alfred/FER. No modificar hash ni script local.

```bash
sudo bash <<'SH'
set -Eeuo pipefail
umask 077
mode=inspect
F=$(mktemp /root/montara-final-activation.XXXXXXXX)
trap 'rm -f "$F"' EXIT
runuser -u sentinelx -- head -c 24001 \
  /srv/montara-core/workspaces/efrain/h2-10-finish-activation.sh > "$F"
test "$(wc -c < "$F")" -le 24000
test "$(git hash-object --no-filters "$F")" = 171f504ccae92c3579a8dd471f5e5b31b116450c
echo REVIEWED_FINAL_ACTIVATION=PASS
/bin/bash "$F" "$mode" </dev/tty >/dev/tty 2>&1
SH
```


## Evidencia y límites

Microgate no privilegiado #10 en Contabo (sólo delta nuevo). Primer job a17eab68e008fa0b290be0fc046acbc383244821a40d697aafd693fc502eab74 EXIT0, syntax/ACL fixture 1+5/alcance. Final job e44af072d203bb5f4fb2d99d8e1805f5adebfb1c487beff3bef2103f09d3bdde EXIT0, blob exacto 171f504..., bash/Python syntax, ACL fixture 2 positivas y 6 negativas (puerto, UID, regla faltante/extra, hook, protocolo), secuencia/alcance inspect sin mutación y dos starts exclusivamente, huella y diagnóstico. ACL JSON fixtures sintéticos, no lectura nft real por runner, no root, no start real, no render/suite H2.9/PR15–19 repetidos. Informes archivados y cola liberada. Comprobación local de systemd-analyze 257 con Conflicts target inexistente rc0 sólo ilustra que ausencia no es por sí un fallo de verificación; destino Ubuntu/systemd real aún por comprobar. El script no soluciona a ciegas una causa histórica no registrada: permite confirmar la causa si persiste y continuar sin reinstalación. QA focalizado del delta si Alfred lo estima material, no reabrir auditorías anteriores. Pendiente inspección/arranque reales y confirmación externa; QA humana FER/JM y Work antes H3.
