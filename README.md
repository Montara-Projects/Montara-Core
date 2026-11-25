# Montara-Core
MONTARA CORE V2 — núcleo independiente.

H1.4: núcleo integrado, proyectos y autoridad de ejecución sintética sobre SQLite. No es un producto listo para exposición pública.

## Desarrollo
Objetivo inicial: Python 3.11, por compatibilidad candidata del Engine estudiado en [BRAIN #3](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/3). Runtime sin dependencias de terceros; backend de build fijado en pyproject.toml. H1.1 no incluye Engine.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -m montara_core --check-config --config config.example.toml
python -m montara_core --config config.example.toml
```

En otra terminal: `curl --fail http://127.0.0.1:8080/health` → `{"status":"ok"}` (espacios JSON no contractuales).
Ctrl+C termina el proceso. Sin configuración explícita se usan defaults seguros. El servidor solo escucha IPv4 loopback y es un smoke de desarrollo, no un servidor de producción.

## Configuración
`--config PATH` selecciona un TOML externo. Campo HTTP: `[http] port`, entero 1–65535 (0 asigna puerto efímero para pruebas). Default 8080; bind fijo 127.0.0.1. Prioridad: defaults < archivo indicado. No leer variables implícitas, secretos o archivos automáticos. Config desconocida, mal formada, inaccesible o inválida: exit 2 antes de escuchar. Error de bind o persistencia: exit 1. `--check-config` no abre sockets ni crea DB.

H1.2 añade opcionalmente `[storage]` con `sqlite_path` absoluto y directorio padre existente, administrado de forma privada por el operador. Sin esta sección, health funciona como H1.1; el servicio de proyectos requiere configuración explícita. Ejemplo (ruta ilustrativa, no se crea automáticamente el directorio):

```toml
[storage]
sqlite_path = "/path/to/private/core.sqlite"
```

Al arrancar con storage configurado se inicializa/verifica la versión SQLite. `PRAGMA user_version=2`, claves foráneas por conexión, transacciones y conexiones cortas; se rechazan versiones desconocidas o bases no vacías sin versión. No reutilizar bases V1.

## Pruebas
```sh
python -m unittest discover -s tests -v
```
CI construye un wheel y ejecuta tests desde una instalación limpia fuera del checkout. Pruebas positivas de arranque/HTTP, negativas de configuración/bind y reglas de dependencias estáticas. No equivalen a instalación VPS ni H2.

## Arquitectura
`application` expone el caso de uso health sin I/O; `api` traduce a HTTP; `bootstrap` valida configuración y ensambla; `__main__` es entrypoint. `domain` contiene Account/Project y reglas puras; `application` contiene los casos de uso y el port ProjectStore; `adapters` implementa SQLite; `bootstrap.projects.build_project_service(config)` ensambla el servicio. Provisionar Account es operación interna, no login. Actor explícito debe proceder de un llamante confiable: no hay autenticación real ni rutas HTTP de proyectos. Los tests crean cuentas sintéticas. Consultas de proyectos siempre acotadas al owner; acceso cruzado y ausencia tienen mismo error. Imports internos absolutos; reglas AST comprobadas en tests. No SDK de proveedor, OIDC, medios, recetas, Engine, Mixer, Companion, scheduler distribuido ni cloud en H1.4. No se reutiliza código V1.

Coordinación: [BRAIN](https://github.com/Montara-Projects/MONTARA-BRAIN), [protocolo](https://github.com/Montara-Projects/MONTARA-BRAIN/blob/main/AGENTS.md), [encargo H1.4 #15](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/15).

## Autoridad mínima H1.3

`bootstrap.jobs.build_job_service(config)` crea el servicio interno. No hay endpoints HTTP, daemon ni autenticación de prueba. El owner se deriva del proyecto; solo un actor de confianza puede invocar create/get/retry/cancel. Claim/finish/recover son operaciones internas de worker/operador, no autoridad para usuarios.

- Create es idempotente por (proyecto, clave de solicitud): no hay otro payload H1.3. Devuelve el estado actual del mismo job al repetir la solicitud. Una clave distinta crea otro job.
- queued → running por claim transaccional; un job running global. Claim devuelve AttemptRef (job UUID, attempt UUID, generación creciente). El intento permanece en el historial.
- running → succeeded/failed por el intento exacto. Resultado sintético único en SQLite, visible solo al commit exitoso. Repetir misma finalización y resultado devuelve el recibo; resultado distinto o intento obsoleto falla.
- Interrupción: un operador/worker confiable recupera la referencia exacta mediante running_attempt (también si se perdió la respuesta del claim) y llama recover; el intento queda interrupted y el job failed. Retry explícito por owner y referencia del intento failed/interrupted → queued; próxima claim crea generación nueva. Sin timeout, lease automático ni exactly-once externo.
- Cancel queued requiere generación observada y ausencia de attempt ID; cancel running exige además el UUID del intento. Cancel es terminal y revoca autoridad de publicar; no promete detener un efecto externo. Repetición exacta es idempotente mientras ese estado siga vigente.
- `application.synthetic_worker.run_once(service)` procesa un trabajo como máximo con resultado determinista; no toca archivos ni ejecuta Engine. Puede invocarse en tests o por un llamante interno.

Migración canónica v1→v2 en una única transacción BEGIN IMMEDIATE: validar origen, crear jobs/attempts/índices, validar destino, preservar proyectos y hacer commit; rollback ante error. Un fichero nuevo pasa por ambas migraciones. Se mantiene rechazo de esquemas no canónicos. No downgrade automático.

## Integración H1.4: API v0 interna de desarrollo

`bootstrap.core.build_core(config)` compone ProjectService y JobService sobre el mismo adaptador SQLite inicializado una sola vez. Los helpers de H1.2/H1.3 delegan en esta composición. Los casos de uso y las transacciones conservan la autoridad existente: no hay segunda base, copia de estado, ni nueva migración.

El contrato es **Python interno**, para código confiable; no es un API HTTP ni un contrato público definitivo. El único endpoint sigue siendo GET /health en loopback, liveness del proceso. No se añade login de prueba ni se aceptan IDs de actor por HTTP. La provisión interna de cuentas solo se usa con fixtures en el recorrido; nunca equivale a autenticación.

Para reproducir el smoke desde el entorno donde se instaló el paquete, ejecutar desde un directorio externo al checkout:

```sh
python -I -m unittest discover -s /path/to/checkout/tests -p test_core_integration.py -v
```

El smoke usa directorios y SQLite temporales, configuración TOML explícita, y procesos Python aislados separados para crear, ejecutar, consultar tras cierre y recuperar un claim perdido. No requiere permisos administrativos ni datos reales.

Ver [contrato de ejecución H1.4 y prerrequisitos H2](docs/execution-contract-v0.md). H1.4 no acredita el primer MP4 ni autenticación, release declarativo o instalación real en el VPS.
