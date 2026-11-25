# Montara-Core
MONTARA CORE V2 — núcleo independiente.

H1.1: paquete ejecutable mínimo, configuración TOML externa, health HTTP local y CI Linux. No es un producto listo para exposición pública.

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
`--config PATH` selecciona un TOML externo. Único campo admitido: `[http] port`, entero 1–65535 (0 asigna puerto efímero para pruebas). Default 8080; bind fijo 127.0.0.1. Prioridad: defaults < archivo indicado. No leer variables implícitas, secretos o archivos automáticos. Config desconocida, mal formada, inaccesible o puerto inválido: exit 2 antes de escuchar. Error de bind: exit 1. `--check-config` no abre sockets.

## Pruebas
```sh
python -m unittest discover -s tests -v
```
CI construye un wheel y ejecuta tests desde una instalación limpia fuera del checkout. Pruebas positivas de arranque/HTTP, negativas de configuración/bind y reglas de dependencias estáticas. No equivalen a instalación VPS ni H2.

## Arquitectura
`application` expone el caso de uso health sin I/O; `api` traduce a HTTP; `bootstrap` valida configuración y ensambla; `__main__` es entrypoint. Domain/adapters aparecerán al existir una responsabilidad real. Imports internos absolutos; reglas AST comprobadas en tests. No SDK de proveedor, base de datos, identidad, medios, Engine, Mixer, Companion, scheduler ni cloud en H1.1. No se reutiliza código V1.

Coordinación: [BRAIN](https://github.com/Montara-Projects/MONTARA-BRAIN), [protocolo](https://github.com/Montara-Projects/MONTARA-BRAIN/blob/main/AGENTS.md), [encargo H1.1 #8](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/8).
