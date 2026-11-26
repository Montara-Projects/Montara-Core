# Release Linux de referencia H2.6 — Core schema4 + Engine real

BRAIN #25. Base histórica exacta `40f5a8e768da7607bc5b9dd94ec299f009bd6861`. No release de producción ni despliegue administrativo. El [gate H1.4](README.md) y sus manifiestos schema2 conservan su evidencia original; no extrapolarla a v4.

## Procedimiento

`reference-h2.6.sh` reproduce el gate bajo el usuario local no privilegiado reservado para pruebas. Requiere HOME temporal privado, MONTARA_OUTPUT dedicado, Ubuntu 24.04 x86-64, git, python3+venv, apt-cache/apt-get/dpkg-deb y salida HTTPS/HTTP a repositorios públicos oficiales. No ejecutar como root ni reutilizar estado real. Se obtiene Core, renderer JM y Zucker por commits exactos, sin submódulos, credenciales ni cambios upstream. El script fija Python 3.12.12 aislado mediante uv 0.9.18; setuptools 75.8.2/wheel 0.45.1, y closure Python observada por Eugenia, descargando wheels y verificando hashes en la instalación offline. Core wheel no incorpora Engine; obtenerlo separadamente. No redistribuir upstream, paquetes multimedia ni dependencias sin revisión de licencias.

FFmpeg/ffprobe 6.1.1 de Ubuntu noble: resolver closure con índices APT locales, descargar y extraer .deb en scratch (no apt install, sin scripts de mantenimiento ni cambios de dpkg); wrappers privados de PATH cargan bibliotecas extraídas, y se documentan dependencias del host. La disponibilidad de versiones exactas en mirrors y hashes debe comprobarse al reconstruir. No es imagen hermética universal. Si faltan versiones, resolver y verificar de nuevo, no sustituirlas silenciosamente.

## Instalación y configuración

Separar config, state (DB, originales, intentos, artefactos) y releases, como en H1.4. Las cuatro rutas son absolutas y separadas; no borrar state junto al slot. Usar TOML externo, puerto de prueba 0, sin servicios públicos ni identidades reales. Construir dos wheels del commit fijado, comparar archivos normalizados, verificar hash, instalar dependencias y wheel en venv limpia y validar desde cwd externo. Activación por reemplazo de enlace current en ventana sin escritores. Las venv se recrean en destino; no son artefactos portables. Los slots son independientes por convención, no inmutabilidad a nivel FS.

El gate canónico positivo adapta el fixture/oracle de tests/engine_h2_smoke.py: dos cámaras 640x360, master PCM, tres cortes de 2s, 1080p30 H264/AAC, semántica stage-engineering-v0 con fundidos y matching, sin reinterpretación de receta. Usa ExecutionSpec real del Core instalado, staging, adaptador real, comprobación de contenido, recuperación de publicación y reapertura en proceso separado. No repite renders de negativos ya acreditados. No equivale a calidad nativa 1080p ni a cargas largas.

## Recuperación

La instalación valida schema4 exacto, reinstala en segundo slot, escribe después de reinstalar, recupera recibo MP4 y escritura posterior tras cambiar al primer slot y volver. Es rollback de instalación del **mismo release**, no de dos versiones de código distintas. El paquete previo schema3 se prueba como incompatible con DB v4. No bajar a schema2 ni v3, no restaurar DB antigua sobre escrituras nuevas. Migración de release v3→v4 y recuperación física de desastre quedan fuera de este gate; conservar estado actual y corregir hacia adelante ante incompatibilidad. Antes de operar en producción se requiere procedimiento de migración explícito y validado con datos.

## OIDC — coordinación Archi #24

Schema4 no incluye OIDC. Para el siguiente gate schema5, integrar configuración externa issuer/endpoints/jwks/client_id/public_origin HTTPS, archivo de secreto privado inyectado fuera repo, extra oidc y closure con hashes, TLS proxy y backend loopback. No generar clientes, secretos ni configuración real de Google en este encargo. Ver [Archi #25](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/25#issuecomment-7180598747).

## Métricas

Wall clock monotónico, CPU user/system agregada de proceso y descendientes recogidos por wait, RSS aproximada muestreada del árbol de procesos, memoria cgroup muestreada (incluye cache, no equivale a RSS), bytes lógicos en state y delta de FS (no I/O físico, incluye actividad ajena). No benchmark ni garantía de capacidad de seis horas; muestreo cada 200ms puede omitir picos y RSS compartida puede contarse varias veces. Separar bootstrap/build/install de fases audiovisuales; comprobar límites observados y margen real. Publicar solo evidencia saneada del fixture, sin identificadores privados ni logs crudos.
