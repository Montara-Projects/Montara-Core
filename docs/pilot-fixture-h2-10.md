# H2.10 — preparación automática de fixture sintético (BRAIN #35)

Sólo el piloto DEVELOPMENT restringido. No es ingestión, upload, Companion ni preparación de datos reales. Core autentica y autoriza, Efrain instala el banco inmutable una vez; el operador no copia archivos ni introduce UUID por proyecto. Se conserva schema5, release histórico y semántica audiovisual. [Encargo](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/35), [contrato Efrain #34](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/34#issuecomment-7238645655).

## Configuración externa opt-in

Dentro de [pilot], junto a OIDC y raíces privadas existentes:
```toml
fixture_bank_root = "/path/to/immutable-fixture-bank-v1"
```
Efrain propone /opt/montara-pilot/fixture-bank-v1; operador decide ruta absoluta canónica existente, separada de DB, originals, workspaces, artifacts y checkouts (no nesting). Banco root:grupo del servicio 0750; tres regulares root:mismo grupo 0440 nlink=1, sin symlinks. Ancestros controlados por operador y no escribibles por servicio/otros. Preflight de instalación coteja permisos y bytes; configuración valida forma/rutas, la acción comprueba contenido/propietario/modo mediante descriptores. No crear banco mediante HTTP, no aceptar manifest ni hash/ruta/nombre libre. No acceso a secretos o capacidad root. La configuración ausente conserva H2.8 legado sin acción automática; [pilot] requiere OIDC, no perfil general de producción. No fallback al mecanismo manual cuando banco configurado falla.

Pines de Core según [bootstrap validado #32](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/32#issuecomment-7230075212):

| Archivo | Tamaño | SHA256 |
|---|---:|---|
| camera-a.mp4 | 63410 | f290175c1fb7f2587baf6e535b0f6adde0ad78b2d31ec250ae027a0590abfbc7 |
| camera-b.mp4 | 63194 | 2817a6ab4b4833f40a5eb9534a90c2c173d3affe408045594f54daa09d87674d |
| master.wav | 576078 | 8ae3abdb2fef54830f15e0177af8daa92cfb8481564fc9822a9acee950eaf33e |

702682 B exactos; se reutilizan bytes verificados, no FFmpeg ni generación en HTTP. Modificar el banco/pines requeriría otro release autorizado.

## Frontera y recuperación

POST /api/projects/{id}/fixture, JSON estricto {}, sesión, Origin exacto y rate de piloto. ID sale del selector de proyecto propio, no se pide UUID a persona. Actor de sesión, UUID validado en servicio, owner antes de cualquier acceso a banco/FS. Con banco habilitado se rechaza registro unitario legado /media (409) para evitar bypass. Sin banco, nueva acción 404 y registro legado permanece. Sin sesión 401; ajeno 404; entrada libre 400; colisión/conflicto/lock ocupado 409; banco/almacenamiento no disponible 503. Respuesta resumen del proyecto con fixture_available y representaciones, sin paths. UI muestra preparación explícita y oculta UUID de operador cuando habilitada.

Core usa una transacción BEGIN IMMEDIATE: comprobar owner, metadata previa exacta, límites, preparar FS, reutilizar lógica de registro verificado de los tres medios en misma transacción. Trabajo pequeño fijo; no otras escrituras concurrentes por HTTP/worker que salten autoridad. El adaptador valida los tres contenidos de banco antes de tocar proyecto, abre directorios/ficheros por descriptor no-follow, crea carpeta 0700 si falta, acepta existente del UID servicio 0700/0750, archivos propios 0400/0440/0600/0640 regulares con un link. Flock del directorio por proyecto (no archivo lock manipulable); DB serializa registros de Core. Validar todos los destinos existentes antes de copiar, no sobrescribir, no hardlink con banco ni entre proyectos, no cambio de metadatos previos incompatibles. Copia exclusiva en .fixture-NOMBRE.partial 0600, fsync, link no-overwrite dentro del proyecto y retirar stage, fsync. Sólo tres nombres finales; los temporales reservados no son originales ni registros. Ante caída, etapa privada regular 0600 del mismo UID y tamaño acotado, sin link externo, se puede retirar y reconstruir; etapa enlazada a final sólo se retira si mismo inode y final verificado byte por byte. Cualquier otra colisión de etapa/final se rechaza sin limpiar. Finales existentes exactos se conservan y faltantes se completan; si falla SQLite después de FS, los tres registros se revierten juntos y los bytes exactos quedan recuperables al repetir. No se promete atomicidad FS+SQLite ni protección ante escritor privilegiado/mismo UID. No borrar archivos ajenos ni cambiar recetas/jobs/resultados. Límites existentes de proyecto/medios, 256MiB/file y 512MiB total, piso libre 2GiB + bytes faltantes antes de copiar (no reserva). Operador mantiene raíces privadas; no otro escritor externo.

## Gate y despliegue posterior

Suite instalada con casos nuevos de cuatro owners/proyectos homónimos, replay/concurrencia, negativas HTTP/owner/Origin/body, parcial exacto, colisión, symlink/hardlink, permisos, banco corrupto, límite, fallo FS/DB, etapa interrumpida y opt-in. Usa bytes sintéticos pequeños y adaptación explícita de UID/pines en tests; verifica pines productivos constantes. No se ejecuta Engine, no sustituye evidencia AV previa, no certifica banco físico de Contabo ni navegador nuevo. Excepción de workflow AV estrictamente para rama propia archi-001/pilot-fixture del repo (encargo #35 sin repetir renders); wheel CI íntegra, gate AV normal y manual permanecen para otras ramas. QA focalizada y despliegue tras autorización FER/Efrain: instalar banco una vez con permisos/pines, nuevo wheel, configurar [pilot], probar acción con cuatro sesiones reales sin pedir UUID ni render; conservar recibos/identidades y comprobar negativa ajena. No permisos efectivos ni host modificados por Archi.
