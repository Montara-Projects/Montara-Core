# Release Linux de referencia — H1.4 / BRAIN #19

Estado: candidato de desarrollo, no release público ni despliegue de producción. El paquete objetivo es el Core de commit `2d5f2fce87838317d717f235ff3675c553755304`; la rama de documentación no se introduce en el wheel. La versión Python del paquete sigue siendo 0.1.0: distinguir releases por commit y SHA-256, no solo por esa etiqueta.

## Reproducción

`reference-h1.4.sh` es el guion completo del gate, ejecutado como trabajo autorizado no privilegiado en Ubuntu 24.04 x86-64. Usa un HOME temporal nuevo, un directorio de salida vacío (variable MONTARA_OUTPUT), la identidad de pruebas montara-test y las herramientas git, python3 con venv y acceso HTTPS a GitHub/PyPI y a las distribuciones gestionadas de Python. No instalarlo como servicio. En otro host Linux se puede reproducir el mismo procedimiento con un usuario local dedicado sin privilegios, adaptando únicamente la comprobación de nombre de usuario del harness. No reutilizar el HOME de trabajo: los entornos y fuentes se crean desde cero.

Se clona el repositorio público sin credenciales, se valida cada commit, se exporta su árbol Git y se construye con Python 3.11.14, uv 0.9.18, setuptools 75.8.2 y wheel 0.45.1. SOURCE_DATE_EPOCH es el timestamp del commit; build sin aislamiento pip adicional, con venv dedicada y dependencias de build fijadas; instalación del wheel offline sin dependencias. Los dos árboles de wheel se comparan por ruta y SHA-256 de cada archivo, no por fechas ZIP. No es un lock de hashes completo de dependencias de bootstrap ni una atestación de cadena de suministro.

## Layout de referencia

```
reference-install/
  config/core.toml
  state/core.sqlite
  releases/<identidad-release>/
    manifest.json
    montara_core-0.1.0-py3-none-any.whl
    venv/
  current -> releases/<identidad-release>
```

Crear config y estado fuera de los slots; TOML explícito con puerto de prueba 0 y sqlite_path absoluto apuntando a state. No guardar DB ni configuración específica de host dentro del wheel. La venv se reconstruye en el destino, nunca se copia desde otro host. El wheel y manifest son los artefactos portables; no la venv.

Procedimiento de instalación (ventana de mantenimiento, sin procesos Core/worker escribiendo): comprobar SHA-256 del wheel y compatibilidad del schema vivo, crear un slot nuevo, instalar con venv limpia y pip --no-index --no-deps, verificar import desde site-packages, pip check y --check-config. Activar reemplazando atómicamente el enlace current dentro del mismo directorio. El guion contiene las funciones de referencia install_slot y activate y prueba este recorrido, no un instalador de producción para datos arbitrarios. Conservar el slot anterior hasta aceptar el nuevo. Los casos de schema existente se limitan a coincidencia exacta en este procedimiento; ninguna migración implícita de release.

## Recuperación

La copia SQLite consistente previa es evidencia/recurso para recuperación de desastre, **no es el mecanismo de rollback del código**. Mantener la DB viva, las escrituras posteriores y configuración externa. Si la versión anterior admite el schema exacto, reactivar su slot tras detener escritores; verificar proyectos, jobs, resultados, integridad y claves foráneas. Volver a la versión nueva sin restaurar la copia. Si no admite el schema, rechazar antes de activar; conservar código compatible y DB actual; corregir hacia adelante o diseñar una migración explícita probada que preserve datos. No restaurar sobre la base actual una copia previa al cambio. Recuperación física de desastre, concurrencia con procesos residentes y migraciones futuras quedan fuera de este gate.

## Engine

Core H1.4 no incorpora Engine ni Mixer; el manifest los mantiene fuera. Coordinación BRAIN #18: pin candidato 9af275b18535005e36bd289a68ceb4a1d2f6c506, Python 3.11 x86-64, closure y licencia pendientes de confirmación, FFmpeg/FFprobe requeridos por el futuro slice (versión y codecs por verificar), 4 GiB provisionales de ejecución y 15 GiB libres de referencia. Un job a la vez, no build/render simultáneo. No redistribuir código de Engine ni introducir dependencias supuestas. El siguiente release debe fijar pines/closure reales y acreditar MP4; no se atribuye esa capacidad a H1.4.

Ver [BRAIN #19](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/19), [BRAIN #18](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/18) y [contrato H1.4](../docs/execution-contract-v0.md).

## Evidencia del gate #19 (26/11/2026)

- [Manifest H1.4](manifests/h1.4.json), [manifest H1.3](manifests/h1.3.json) y [resultado del gate](manifests/validation-h1.4.json) generados por la ejecución real.
- Dos árboles de fuente independientes exportados del mismo commit, toolchain común fijada. 29 archivos de wheel coincidentes y SHA-256 binario idéntico: `672c5907d97ff6a2577ff0e85fd3baed664530fb210d73ffaf0aac4ee8e86128`. Python 3.11.14, SQLite 3.50.4, pip 24.0. No se acredita reproducción en otro proveedor ni build con toolchain distinto.
- Reinstalación en nuevo slot: árbol instalado de Core y dist-info igual, excluyendo bytecode/cache y metadatos de instalación dependientes del origen (RECORD y direct_url.json); no comparar bytes de toda la venv. Slots separados por convención, no filesystem inmutable. Config hash y DB inode conservados; dump lógico, integridad y FK verificados antes/después de los cambios.
- H1.3→H1.4→reinstalación→H1.3→H1.4: cuatro proyectos/jobs/resultados sintéticos, incluidas escrituras tras upgrade, reinstalación y rollback, presentes al final. H1.2 real rechaza schema v2; guard de instalación rechaza schema y hash incorrectos antes de crear slot; no se restaura el snapshot anterior. 45/45 pruebas originales desde paquete instalado (7.806 s, no benchmark), exit 0.
- Artefactos wheel actual y anterior retenidos en almacenamiento de pruebas V2; no release pública de binarios. Entorno de instalación temporal, no servicio residente ni despliegue administrativo.
- Capacidad observada: 4 CPU afinidad, ~7.3 GiB RAM disponible, 93.3 GiB disco libre, cgroup memory.max 4 GiB/pids.max 256; cpu.max no legible. FFmpeg/FFprobe no encontrados en PATH de ejecución; no se probaron codecs ni carga Engine.

Actualización de dependencia, posterior a la generación de los manifests: [Eugenia #18](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/18#issuecomment-7178659563) acredita ruta real en Ubuntu 24.04 x86-64 con Python 3.12 y FFmpeg 6.1.1-3ubuntu5, perfil H.264/AAC, lavfi y ffprobe, dependencias observadas allí. Reproducir primero ese cierre en entorno aislado 3.12; no cambiar el runtime Core 3.11 validado ni fijar dependencias no probadas. El pin candidato de Engine fue usado por Eugenia; distribución/lock final y prueba integrada en VPS siguen pendientes. Ver [coordinación de Efrain](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/18#issuecomment-7178715725).

## Alcance tras integración H2.1 (schema3)

Esta carpeta conserva evidencia histórica del release **H1.4/schema2**, no del HEAD actual de `main`. Desde [PR #6](https://github.com/Montara-Projects/Montara-Core/pull/6), `main` (`6801203e253a07ccfffa1a6533720c6485300996`) incorpora schema3 y migración v2→v3. La CI del paquete integrado no sustituye el gate de release del schema3. El guion y los tres manifiestos permanecen fijados a los commits históricos; no cambiar sus pines ni reutilizar sus hashes como evidencia del nuevo Core. **No se autoriza ni se acredita rollback v3→v2**: con una DB v3, rechazar activación de los slots H1.3/H1.4 según la regla de coincidencia de schema, conservar DB actual y código compatible; diseñar y validar una recuperación nueva si procede. No restaurar un snapshot v2 sobre escrituras v3. Release/instalación y recuperación del schema3: NOT RUN en este encargo; Engine tampoco se instala. Véase [contrato H2.1](../docs/engine-contract-h2-1.md).
