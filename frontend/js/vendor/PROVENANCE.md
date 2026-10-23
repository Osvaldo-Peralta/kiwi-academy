# Dependencias vendorizadas — procedencia e integridad

Archivos de terceros incluidos en el repositorio (sin build step, sin CDN en
runtime). Cada actualización debe regenerar este documento con sus hashes.

## supabase-js 2.118.0 (cliente oficial de Supabase Auth)

| Campo | Valor |
|---|---|
| Archivo | `supabase-js-2.118.0.js` (bundle UMD, expone `window.supabase`) |
| Paquete | `@supabase/supabase-js@2.118.0` (dist-tag `latest` al 2026-10-23) |
| Procedencia | tarball oficial del registro npm: `https://registry.npmjs.org/@supabase/supabase-js/-/supabase-js-2.118.0.tgz` (archivo interno: `package/dist/umd/supabase.js`) |
| Licencia | MIT |
| Integrity sha512 (publicada por el registro) | `sha512-FxI8UXMVC2UG7IkeiI8fuQZqq2oI3JeSuN8MPIC2RLVaoEAxKS0nqE6kii9Ewpq8f9gNPM6git1Y8f0vN/QLFw==` |
| sha512 del tarball descargado (verificado) | idéntico al publicado por el registro ✅ |
| sha256 del archivo vendorizado | `ee3be8de192b97cb1d73d1e95d6b82bb1f6b658c0f97c4ef757fc331996ed3d3` |
| Tamaño | 218.252 bytes |
| Advisories (OSV.dev, 2026-10-23) | 0 vulnerabilidades conocidas para `@supabase/supabase-js@2.118.0` (ecosystem npm) ✅ |

### Verificación en cualquier momento

```bash
sha256sum frontend/js/vendor/supabase-js-2.118.0.js
# debe imprimir: ee3be8de192b97cb1d73d1e95d6b82bb1f6b658c0f97c4ef757fc331996ed3d3
```

## Dependencias Python añadidas en Iteración 1 (no vendorizadas, vía pip)

| Paquete | Versión | Licencia | sha256 del wheel (PyPI) | OSV |
|---|---|---|---|---|
| PyJWT | 2.15.1 | MIT | `42d59d631f7768a1028a64c7ff581a9bf7519804daf91fc5b6c56e30eec5e193` (pyjwt-2.15.1-py3-none-any.whl) | 0 vulnerabilidades ✅ |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | ver `https://pypi.org/pypi/cryptography/50.0.2/json` (múltiples wheels por plataforma) | 0 vulnerabilidades ✅ |

Justificación de necesidad (R-03): la arquitectura aprobada exige verificación
de JWT de Supabase en el backend, incluyendo signing keys asimétricas (JWKS,
ES256/RS256). PyJWT es el estándar de facto en Python para esa tarea y su
extra `[crypto]` aporta `cryptography` para los algoritmos asimétricos.
