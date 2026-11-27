# H2.10 — Continuación focalizada tras ffmpeg_local_closure

[Incidencia Alfred-002 #32](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/32#issuecomment-7226688556), main 76af94d101b6c03627b561fbd03897a6838f8027, PR17 integrada. FER reportó REVIEWED_INSTALLER, PREFLIGHT_LOCAL, ARCHIVED_WHEEL_AND_CONNECTOR_HASH, CONNECTOR_VERSION_AND_TOKEN_FILE, sources, python_bootstrap PASS; ffmpeg_local_closure apt-get download rc100 por libasound2-data y libasound2t64 1.2.11-1ubuntu0.3; PILOT_STOPPED_REVIEW_REQUIRED. No relanzar instalación inicial. La posición de fallo se deduce del código y salida humana. No se inspeccionó directamente el slot: SentinelX rechaza /opt/montara-pilot con path_not_allowed. No ampliar permisos ni usar otro host como puente. El preflight administrativo siguiente debe comprobar estado real y detenerse ante divergencia, no presumir que los directorios/unidades son como esperamos.

## Recuperación exacta, sin cambios de lock

Lock aprobado release/manifests/h2.6-ffmpeg-lock.json, blob Git f425eed4a28ef2c5bb60a042c0ffbfe9d11b59e6, 148 paquetes y 75 bibliotecas host. Los manifiestos históricos están archivados pero no se encontró closure binaria anterior en el inventario autorizado de artefactos. En Contabo, cola no privilegiada #10, ambas versiones devolvieron 404 en archive.ubuntu.com y security.ubuntu.com; recuperadas por HTTPS en:

https://snapshot.ubuntu.com/ubuntu/20261126T100000Z/pool/main/a/alsa-lib/

- libasound2-data_1.2.11-1ubuntu0.3_all.deb, 21350 B, SHA256 0c26896193480d9dbb055e85e8ddfb9c9f34cf19afee340987cd6311e2886fc1.
- libasound2t64_1.2.11-1ubuntu0.3_amd64.deb, 397772 B, SHA256 5d17bfb5683eb99f1be951fe48e1be5bf3c52391913b6b74a8aecf9b4f5e779f.

Los otros 146 se obtuvieron mediante apt-get download con los pines originales, rc0. Verificación completa nombres/tamaño/SHA256: 148/148, missing/drift/extras=0, 110780920 bytes; JOB_EXIT=0, SHA de job 06a3639d6b662d7c361206fb49d73a1b97ab5af0d5ce4e08f45eae56a1bcc93d. No apt update/install/upgrade ni extracción en host durante recuperación. El snapshot suministra bytes HTTPS comparados con lock previamente aprobado (originalmente APT índices firmados); no se hizo nueva verificación independiente de índice firmado del snapshot. [Servicio oficial](https://snapshot.ubuntu.com/) y [documentación Ubuntu](https://ubuntu.com/server/docs/how-to/software/snapshot-service/). No cambiar lock ni descargar una versión sustituta. Closure archivada privadamente en workspace de artefactos autorizado, inventario de 148/110780920 comprobado tras mover; raíz y acceso real verificados al copiar mediante hashes de nuevo. No publicar binarios ni recursos privados en GitHub. No coste nuevo.

## Continuación revisada

[resume-pilot.sh](resume-pilot.sh) y [resume-runtime.sh](resume-runtime.sh), sólo para este fallo antes del sellado. No reemplazar ni editar scripts originales aprobados. El operador verifica staging, hashes y estado parcial: UIDs/grupos, rutas y propietarios, configuración sin secretos, state y work vacíos, sin current/ACL/unidades/listeners, procesos de los tres UIDs ausentes, control scripts originales exactos, wheel y conector originales por SHA. Si falta algo o hay un recurso activo, parar y revisar; no borrar ni relanzar instalación. Tras CONTINUAR autorizado, copiar los 148 .deb usando el lector sentinelx autorizado, uno por uno, con límite exacto de bytes y SHA256 verificados, cache root:grupo servicio 0440. No ejecutar scripts desde workspace mutable ni como root ejecutar código de slot. El cache local de input queda conservado.

Como UID montara-pilot, resume-runtime revisa métricas previas de las tres fases (dos PASS y fallo de FFmpeg), checkouts canónicos, pines y limpieza, blob lock, hash de wheel uv, versión uv y Python 3.12.12 existente bajo slot. No reconstruir fuentes, reinstalar Python ni mover venv. Python bootstrap original no tiene hash de toda distribución Python (se conserva la existente, no se afirma un nuevo hash). Inventario top-level esperado; si otro contenido, detener para revisión. Conserva métricas anteriores, nuevo release-resume.py y resume-evidence. Preserva debs parcial sin extraerlo, usa sólo closure archivada y comprueba los 148 nombres/tamaños/hashes/metadatos dpkg antes de extraer ninguno. Extracción local, wrappers, ffmpeg/ffprobe versión/encoders, binarios exactos y 75 hashes host con ldd como gate original, no render. Si hay drift host, detener, no actualizar indiscriminadamente. Continúa fase Python wheel closure, exactos 22 wheels y locks, instalación del wheel aprobado offline y pip check/import/UI; no suite ni render. El código desde fase Python wheel closure es idéntico al original excepto nombre de script launcher. El bloque raíz desde sellado es idéntico byte por byte al aprobado PR17: symlinks/Git post-seal, current, prompts privados, ACL, enrolamiento de dos sujetos, activación explícita; no salto de gates ni nuevas credenciales. Queda pendiente acreditar todo ello en destino.

Si una nueva interrupción ocurre, cleanup de PR17 intenta detener sólo unidades V2, conserva estado/cache/evidencia; si no confirma parada, Alfred deja edge inerte. No reejecutar este bloque (guards de estado parcial y cache), no borrar slot ni restaurar datos; revisar el nuevo punto y preparar continuación focalizada. No asumir que el proceso ha corrido hasta el final. Si fallo en preflight sin mutación, revisar inventario privado; no relajar checks a ciegas. El original instalador no es un recovery script. No hay intervención root hasta revisión de delta y autorización FER.

## Bloque único de continuación, no ejecutar aún

Reutiliza el bundle inicial PR17 13 archivos, más bundle de continuación de dos scripts y lock original aparte (readback exacto). El loader copia como sentinelx, verifica nombres y blobs, comprueba hash fijo de ambos entrypoints y lock; todos los demás archivos se comprueban por resume-pilot antes de actuar. Valores privados sólo en terminal. Alfred confirma preflight pendiente y edge inerte; FER autoriza. No introducir secretos en este bloque ni publicar salida sensible.

```bash
sudo bash <<'SH'
set -Eeuo pipefail
umask 077
D=$(mktemp -d /root/montara-resume-reviewed.XXXXXXXX)
trap 'rm -rf -- "$D"' EXIT
W=/srv/montara-core/workspaces/efrain
for name in h2-10-initial-two-bundle.json h2-10-resume-bundle.json h2-10-ffmpeg-original-lock.json; do
  runuser -u sentinelx -- head -c 65001 "$W/$name" > "$D/$name"
  test "$(wc -c < "$D/$name")" -le 65000
done
python3 -I - "$D" <<'PY'
import hashlib,json,sys
from pathlib import Path
d=Path(sys.argv[1])
sets={
"h2-10-initial-two-bundle.json":{"prepare-runtime.sh","render-config.py","pilot.example.toml","gitconfig","check-sealed-git.sh","montara-pilot-http.service","montara-pilot-worker.service","enrol.py","montara-pilot-enrol.service","montara-pilot-ingress.service","montara-pilot-acl.service","install-pilot.sh","enrol-batch.sh"},
"h2-10-resume-bundle.json":{"resume-runtime.sh","resume-pilot.sh"}}
for name,names in sets.items():
    o=json.loads((d/name).read_text())
    assert o["format"]==1 and len(o["files"])==len(names)
    assert {x["name"] for x in o["files"]}==names
    for x in o["files"]:
        b=x["content"].encode()
        assert hashlib.sha1(b"blob "+str(len(b)).encode()+b"\0"+b).hexdigest()==x["sha"]
        with (d/x["name"]).open("xb") as f:f.write(b)
(d/"h2-10-ffmpeg-original-lock.json").rename(d/"ffmpeg-original-lock.json")
PY
test "$(git hash-object --no-filters "$D/install-pilot.sh")" = 91e64d5cb8aa58712efefec3a9346b5d267d0a52
test "$(git hash-object --no-filters "$D/resume-pilot.sh")" = 3d9309224c9ff1c49a4ef1b4c75702399423c2bb
test "$(git hash-object --no-filters "$D/resume-runtime.sh")" = 8b301272c8df27cd7a39773241e79d0a6c454fc9
test "$(git hash-object --no-filters "$D/ffmpeg-original-lock.json")" = f425eed4a28ef2c5bb60a042c0ffbfe9d11b59e6
echo REVIEWED_RESUME=PASS
/bin/bash "$D/resume-pilot.sh" "$D" </dev/tty >/dev/tty 2>&1
SH
```


## Evidencia y reproducibilidad posterior

Microgate estático Contabo #10, exactos cuatro blobs, bash -n y compile de Python embebido, igualdad tail post-seal y Python closure y guard preextracción, ausencia de reconstrucción sources/bootstrap: RESUME_STATIC_MICROGATE=PASS, JOB_EXIT=0, job SHA 319380b99db1c881bc8d9c5a1328553eda105c707250a73b181ce3a45f0b12a5. No ejecución de instalador, root preflight, ldd real ni Python closure nueva; se harán en continuación. No pruebas H2.9/PR16/PR17 ni render repetidos. Informes privados archivados, cola idle. Bundle 2 scripts 29940 caracteres y lock 54163, readback exacto; inicial PR17 intacto. No tocar V1/Cloudflare/Google. QA sólo delta, CI automática.

Para futuras instalaciones, conservar cache binaria 148 .deb con el lock original y verificación obligatoria en cualquier copia, no depender de disponibilidad de índices vivos. Mantener archivo privado bajo control del proyecto, transferencia a otro host autorizada y validada; copia única Contabo no equivale a backup fuera del proveedor. Recomendación posterior: integrar modo offline-first de closure en instalador general después de cerrar piloto, archivar también wheels y distribución Python con procedencia/hash y evaluar licencias de distribución antes de mover/publicar. No introducir R2 ni gastos en este gate. El instalador inicial histórico sigue usando APT, no afirmar que ya es hermético; esta recuperación usa cache offline para multimedia y conserva los pines del resto. No certificar segundo host, recuperación de desastre ni capacidad audiovisual larga.
