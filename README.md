# Montara-Core
MONTARA CORE V2 — núcleo independiente.

H1.2: paquete ejecutable, proyectos internos y persistencia SQLite local. No es un producto listo para exposición pública.

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

Al arrancar con storage configurado se inicializa/verifica la versión SQLite. `PRAGMA user_version=1`, claves foráneas por conexión, transacciones y conexiones cortas; se rechazan versiones desconocidas o bases no vacías sin versión. No reutilizar bases V1.

## Pruebas
```sh
python -m unittest discover -s tests -v
```
CI construye un wheel y ejecuta tests desde una instalación limpia fuera del checkout. Pruebas positivas de arranque/HTTP, negativas de configuración/bind y reglas de dependencias estáticas. No equivalen a instalación VPS ni H2.

## Arquitectura
`application` expone el caso de uso health sin I/O; `api` traduce a HTTP; `bootstrap` valida configuración y ensambla; `__main__` es entrypoint. `domain` contiene Account/Project y reglas puras; `application` contiene los casos de uso y el port ProjectStore; `adapters` implementa SQLite; `bootstrap.projects.build_project_service(config)` ensambla el servicio. Provisionar Account es operación interna, no login. Actor explícito debe proceder de un llamante confiable: no hay autenticación real ni rutas HTTP de proyectos. Los tests crean cuentas sintéticas. Consultas de proyectos siempre acotadas al owner; acceso cruzado y ausencia tienen mismo error. Imports internos absolutos; reglas AST comprobadas en tests. No SDK de proveedor, OIDC, medios, recetas, jobs, Engine, Mixer, Companion, scheduler ni cloud en H1.2. No se reutiliza código V1.

Coordinación: [BRAIN](https://github.com/Montara-Projects/MONTARA-BRAIN), [protocolo](https://github.com/Montara-Projects/MONTARA-BRAIN/blob/main/AGENTS.md), [encargo H1.2 #11](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/11).
