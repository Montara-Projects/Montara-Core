# Contrato interno de ejecución — H1.4

Estado: contrato de desarrollo sintético, no freeze de API pública ni habilitación de Engine.
Referencia: [ADR #415](https://github.com/Montara-Projects/Montara/issues/415#issuecomment-7141340666), [ACK FER](https://github.com/Montara-Projects/Montara/issues/415#issuecomment-7150570278), [ACK técnico JM](https://github.com/Montara-Projects/Montara/issues/415#issuecomment-7151607436), [inventario H0 Eugenia](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/1#issuecomment-7150771137) y [BRAIN #15](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/15).

## Contrato ejecutable hoy

| Responsabilidad | Entrada / operación | Salida y autoridad |
|---|---|---|
| Composición | Config externa con sqlite_path, build_core(config) | CoreServices.projects y CoreServices.jobs, un SQLite |
| Provisión interna | projects.provision_account() | UUID opaco; no login ni prueba de identidad |
| Proyecto | create_project/get_project/list_projects(actor, ...) | owner validado por Core; acceso cruzado rechazado |
| Solicitud | jobs.create_job(actor, project_id, request_key) | Job persistido; replay por proyecto+clave devuelve el mismo job |
| Consulta | jobs.get_job(actor, job_id) | Estado, generación, intento y resultado sintético autorizado |
| Worker confiable | jobs.claim() | AttemptRef(job_id, id, generation) o None; un job running global |
| Ejecución sintética | application.synthetic_worker.run_once(jobs) | claim y succeed con resultado determinista en DB; ninguna transferencia ni archivo |
| Finalización | jobs.succeed(ref, result) / fail(ref) | transición y resultado atómicos; recibo exacto repetible, conflicto/obsoleto rechazado |
| Recuperación | running_attempt(), recover(ref), retry(actor, ref) | recuperación explícita, revoca intento; retry genera nueva referencia al reclamar |
| Cancelación | cancel(actor, job_id, generation, attempt_id) | queued requiere generación observada y sin attempt_id; running requiere intento exacto; terminal |
 
Los IDs de actor se reciben exclusivamente de llamante interno confiable, no de HTTP, headers, parámetros CLI o identidad inferida. Claim/finalización/recuperación son capacidades internas del worker/operador, no endpoints de usuario. No se modela autenticación en H1.4. Las excepciones de dominio/storage/transition controlan errores; no hay códigos HTTP de estas operaciones. GET /health en loopback no es readiness dinámica ni autentica al cliente.

La DB v2 y el estado del Core son única autoridad. Sin nuevas tablas, permisos, servicios residentes, cuotas ni rutas. La transacción de finalización impide doble publicación lógica de un resultado sintético. La recuperación se activa por operador que determina que el intento dejó de ser válido, no por timeout automático. No se garantiza exactly-once de efectos externos ni que un proceso anterior haya terminado físicamente.

## Puente hacia H2: requisitos pendientes obligatorios

El modelo directo Job→Attempt solo representa una operación sintética. **Antes de ejecutar etapas reales** debe evolucionar sin bifurcar la autoridad: Job→Task→Attempt persistidos, con intento exacto por Task y snapshot inmutable de RecipeRevision, inputs/revisiones de representación autorizados y perfil de salida; dependencias intra-job únicamente cuando el slice las necesite. No congelar el contrato sintético como API Engine ni implementar ahora DAG/planificador distribuido.

Contrato objetivo (propuesta de semántica, no tipos/firmas definitivos):

- Core resuelve identidad/owner y permisos, captura receta/snapshot, reserva admisión/espacio conforme límites acordados, crea Task(s), emite referencia de intento exacta con generación, especificación inmutable y entradas verificadas. El adaptador no acepta paths suministrados como prueba de autorización ni sustituye originales por proxies.
- EngineAdapter recibe especificación y workspace privado del intento, ejecuta capacidades reutilizadas según inventario H0 con versiones/closure/licencias revisadas, devuelve candidato (resultado temporal, manifest/procedencia y errores controlados). No escribe estados de Job/Task, no administra permisos/cuotas, no publica resultado visible, no cambia recetas. No invocar WizardRunner como autoridad V2.
- Core verifica bytes/digest/tamaño, tipo y calidad acordada, permisos/admisión y fencing del intento al commit; promueve artefacto verificado de forma reconciliable y registra un único resultado visible. Cancel/recovery revocan autorización de publicación; reintento crea intento distinto, nunca reutiliza un output obsoleto. Persistir receipt y reconciliar fallos antes/después del commit sin perder escrituras posteriores. No se acreditan todavía estos efectos reales.
- H2 requiere OIDC real (issuer+subject), ownership y fuentes/revisiones/ubicaciones autorizadas, originales cloud opt-in y cloud apagado para fixture Linux; contrato de receta temporal, Task/Attempt y admisión necesarios, publicación/artefacto real, revisión de privacidad y calidad. Engine pin definitivo y pruebas con fixtures autorizadas según Eugenia/QA. No paridad V1 ni transferencia de originales por defecto.
- Efrain: gate separado de paquete/release declarativo, instalación objetivo y recuperación que preserve nuevas escrituras. Actions desde wheel acredita H1.4, no instalación persistente VPS, DR integral ni MP4 reproducible.

Secuencia acordada para primer slice: Login → Proyecto → Medios → Receta → Job → Engine → MP4 → Reapertura. H1.4 solo demuestra el tramo interno Proyecto → Job → ejecución sintética → resultado → reapertura/recuperación. Los requisitos anteriores siguen abiertos; no declarar cumplimiento completo de ADR.
