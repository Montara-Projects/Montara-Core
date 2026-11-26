# H2.5 — OIDC y HTTP mínimo de ingeniería

[BRAIN #24](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/24) / [ADR #415](https://github.com/Montara-Projects/Montara/issues/415#issuecomment-7141340666). Mononodo, un proceso. No API pública de producción, no editor, ni rutas de ejecución interna. Mantiene el pipeline y la autoridad de publicación H2.4. OIDC es opt-in; configuración sin sección oidc permanece health-only. No creación de clientes ni exposición de infra.

## Identidad y ceremonia

Authorization Code, confidential client, scope openid, PKCE S256, state y nonce aleatorios por transacción. Cookie __Host-montara-login de 5 minutos, Secure/HttpOnly/SameSite=Lax/Path=/, sin Domain. Estado/nonce/verifier permanecen server-side; callback exige cookie y state, consume una vez incluso si falla; intercambio HTTPS con token endpoint fijo, client_secret y verifier. Backchannel TLS con CA del sistema, no redirects/proxy ambiental, timeout, tamaño limitado y JSON sin claves duplicadas. Endpoints e issuer HTTPS explícitos del operador, no discovery controlado por cliente. Usar [discovery oficial Google](https://accounts.google.com/.well-known/openid-configuration) para corroborar metadata externa. Authorization endpoint es redirección fija, no URL supplied por usuario. No guardar tokens Google, no usar access_token como identidad.

[PyJWT 2.15.1](https://pypi.org/project/PyJWT/2.15.1/) + cryptography mediante extra oidc; únicamente RS256, kid único en JWKS del issuer configurado, RSA ≥2048, sin jku/jwk/x5u/crit externos. Valida firma, issuer exacto, audience, exp/iat/nbf si presente, nonce, azp (obligatorio para múltiples audiences y exacto si presente); iat máximo 10min, sin leeway. Google issuer configurado https://accounts.google.com; no aceptar alias arbitrario. No criptografía JWT propia. Referencias [Google OIDC](https://developers.google.com/identity/openid-connect/openid-connect), [RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html). Claves JWKS se consultan por login, rotación soportada sin cache prematura; límite y timeout por respuesta, no garantizar SLA. Firma no autoriza proyecto.

Schema5 aditivo `oidc_identities(issuer,subject,account_id)`, PK issuer/sub y UNIQUE account_id; creación/resolución bajo BEGIN IMMEDIATE, UUID4 opaco en accounts. No email/profile persistidos; mismo subject con distinto issuer es identidad diferente; mismo email con sujetos distintos no une cuentas. No vinculación a cuentas H1 sintéticas ni linking multi-IdP aún. Migración v4→v5 preserva datos y rollback transaccional. No downgrade; Efrain #25 acredita v4 histórico, siguiente release v5 requiere su propio gate.

Sesión opaca 256 bits, hash en memoria, TTL absoluto 1h, sin refresh, login rota sesión presentada y logout invalida server-side. Capacidad 1024 transacciones y sesiones por proceso con expiración; restart invalida todas las cookies, nueva autenticación conserva UUID. Cookies __Host- con Secure/HttpOnly/SameSite=Lax; Origin exacto requerido en POST, JSON estricto y máximo 128 KiB, sin CORS. Callback GET protegido por state y binding de cookie. Sin tokens ni paths en logs, no-store, CSP restrictiva, nosniff, referrer no-referrer. Autenticación no equivale a gestión de cuentas/admin; piloto requiere acceso restringido en IdP y proxy. No afirmar protección frente a host comprometido, DoS de red, XSS de aplicaciones ajenas, revocación global instantánea por proveedor, ni multiinstancia.

## Superficie mínima

| Método | Ruta | Cuerpo / resultado |
|---|---|---|
| GET | /health | liveness sin identidad |
| GET | /auth/login | redirect IdP + cookie transacción |
| GET | /auth/callback?code=…&state=… | redirect /api/me + cookie sesión |
| POST | /auth/logout | {} |
| GET | /api/me | account_id |
| GET/POST | /api/projects | lista / {name} |
| GET | /api/projects/{id} | proyecto propio |
| POST | /api/projects/{id}/media | {kind,locator} |
| GET | /api/representations/{id} | representación propia |
| POST | /api/projects/{id}/recipes | {expected_revision,master,shots:[{representation_id,start_ms,end_ms}]} |
| GET | /api/recipes/{id} | receta propia |
| POST | /api/projects/{id}/jobs | {key,recipe_id} (export obligatorio) |
| GET | /api/jobs/{id} | estado/resultado propio |
| GET | /api/jobs/{id}/artifact | MP4 committed, attachment, no-store |

Mutaciones requieren sesión y Origin del public_origin; no aceptar owner/actor de parámetros, Authorization Bearer, X-Actor o cookies sin sesión. Respuesta 401 no autenticado/Origin inválido; 404 ajeno igual que inexistente; 400 entrada incorrecta; 409 conflicto o no publicado; 503 fallo de almacenamiento. Rutas exactas y sin query salvo callback; sin static root, Range, upload, cancel/retry ni endpoints de worker. Registro media sólo locator relativo bajo carpeta de proyecto de medios previamente colocados por operador de confianza; Core conserva validación/ownership/checksum/symlinks. No exponer rutas locales de MediaLocation ni ArtifactReceipt. Descarga comprueba estado committed, owner y bytes, abre y verifica descriptor regular exacto y transmite ese descriptor, no path solicitado; directorios privados controlados por operador, no sandbox ante escritor local. Una descarga puede bloquear el servidor mononodo; no benchmark, streaming concurrente, límites de tasa ni servidor de producción todavía. No exponer directamente http.server.

Job queda queued. Worker de confianza interno reclama (core.jobs.claim), usa owner autorizado del proyecto y publication.execute(owner,ref,adapter); no se permite que usuario reclame, publique o suplante actor. Fixture de prueba HTTP publica mediante adapter de bytes para acreditar auth/aislamiento y descarga, no calidad MP4; gate audiovisual H2.4 usa Engine real por separado. Para gate integrado real, operador coordina un worker aislado con los pines de H2.4 y prueba con fixture autorizado.

## Configuración / instalación / gate Google real

Extra `pip install 'montara-core[oidc]'` del wheel aprobado, resolver y congelar closure de PyJWT[crypto] por Efrain en release schema5 (Python 3.11/3.12). CI instala extra para verificar firma; wheel base conserva no deps para health y Engine histórico. `--check-config` valida estructura y roots sin leer secretos ni bind; arranque OIDC exige file absoluto canónico, regular, owner service UID y 0600, contenido secreto solo, sin JSON ni log. No incluir secreto en TOML, argv, env, GitHub ni artefactos. [Coordinación #25](https://github.com/Montara-Projects/MONTARA-BRAIN/issues/25#issuecomment-7180598747).

1. FER autoriza cliente Google OAuth Web independiente de V1, acceso restringido a cuentas de prueba FER/JM, consentimiento openid, redirect exacto `https://<LAB-V2>/auth/callback`. No tocar clientes V1, no publicar secretos. Operador confirma metadata endpoints HTTPS y configura [oidc] según config.example.toml.
2. Proxy TLS exclusivo LAB, preservar Host exacto public_origin y ruta/query callback, no cache ni logs query/cookies/codes, limitar tamaño/tasa/timeout, bloquear acceso directo al loopback desde otros usuarios no confiables, sin CORS añadido. Core no confía en X-Forwarded-* ni deriva issuer/redirect/actor del request. TLS y proxy real son gate de instalación, no acreditados por tests. No usar dominios reales ni publicar infra en GitHub.
3. Arrancar paquete aislado con config externa. Abrir /auth/login en dos perfiles navegador separados; verificar /api/me identifica dos UUID distintos, repetir login conserva UUID. Crear proyectos independientes, verificar acceso cruzado 404 y descarga 401/404. Preparar fixture autorizado por proyecto y worker, confirmar MP4 publicado/reopen y descarga SHA. Logout y reinicio invalidan sesión, no proyectos. Gate real Google/HTTPS extremo a extremo NOT RUN hasta autorización y credenciales. No simular ACK.

## Límites

No se ha ampliado catálogo audiovisual, receta o semántica Stage, ni se han cambiado V1/Engine/release histórico. Antes de público: rate limit/abuse, admisión/cuotas, retención/GC, política de cuentas/linking, DR, endurecimiento/servidor y reverse proxy, cierre de dependencias/licencias, validación real FER/JM. No producción con el piloto.
