# TOROIN Plataforma · Backend

API **multiempresa (marca blanca)** en FastAPI. Cada empresa tiene su marca, sus usuarios, sus **módulos activables** y su **licencia** (prueba, periodo, hasta fecha o ilimitada). El super admin de la plataforma lo gobierna todo desde `/platform`.

Frontend: [front_toroin](https://github.com/AlexEspinosa98/front_toroin) (Next.js).

## Qué incluye

| Área | Rutas | Notas |
|---|---|---|
| Autenticación | `POST /auth/login`, `GET /auth/me`, `POST /auth/cambiar-password` | JWT. El login lleva el `slug` de la empresa; sin slug = plataforma |
| Marca pública | `GET /public/empresas/{slug}/marca` | Para pintar el login con la marca antes de entrar |
| Super admin | `/platform/*` | Dashboard, empresas, marca, módulos, licencias, planes, suplantación, parámetros legales, auditoría |
| Admin de empresa | `/empresa/*` | Marca propia, usuarios, consulta de licencia y uso |
| Negocio | `/obras`, `/personal` | Base para portar el resto de módulos del prototipo |

### Reglas de licencias (`app/licensing.py`)
- Estados: `prueba`, `activa`, `por_vencer` (≤30 días), `gracia`, `vencida`, `suspendida`, `cancelada`, `sin_licencia`.
- `vencida` = **solo lectura** (HTTP 402 al escribir); `suspendida`/`cancelada` = sin acceso (403). Los datos nunca se borran.
- Módulos efectivos = módulos del plan + control por módulo (activar, desactivar o vigencia propia). Si falta una dependencia, el módulo dependiente se apaga.
- Límites (`max_usuarios`, `max_obras`, `max_trabajadores`) vienen del plan y se pueden sobrescribir por licencia.
- Todo se valida **en el servidor**: `exigir_modulo("clave")` en `app/deps.py`.

### Suplantación («ver como la empresa»)
`POST /platform/empresas/{id}/impersonar` exige un motivo, entrega un token de 60 min, **solo lectura por defecto**, y queda en auditoría. Hay datos personales de trabajadores (Ley 1581 de 2012).

### Aislamiento
Cada tabla de negocio lleva `empresa_id`; toda consulta filtra por la empresa del token y los accesos por id a otra empresa devuelven 404 (hay tests de fuga entre empresas).

## Ejecutar en local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # ajusta JWT_SECRET y el super admin
alembic upgrade head            # crea las tablas (SQLite por defecto)
python -m app.seed --demo       # planes, parámetros legales, super admin y empresa «demo»
uvicorn app.main:app --reload   # http://localhost:8000/docs
pytest                          # 13 pruebas
```

Empresa demo: slug `demo`, `admin@demo.com` / `Demo12345` (solo para desarrollo).

## Base de datos: Supabase (o cualquier PostgreSQL)

Se usa **solo como PostgreSQL** mediante SQLAlchemy, sin SDK propietario, para poder migrar cambiando una variable.

1. En Supabase: *Project Settings → Database → Connection string → Transaction pooler* (puerto 6543).
2. Ponla en `DATABASE_URL` (acepta `postgresql://...`, se adapta a psycopg 3 sola).
3. Crea las tablas y los datos base **desde tu máquina** apuntando a esa base:
   ```bash
   DATABASE_URL='postgresql://...' alembic upgrade head
   DATABASE_URL='postgresql://...' SUPERADMIN_EMAIL=tu@correo.com SUPERADMIN_PASSWORD='clave-larga' python -m app.seed
   ```
4. Las **imágenes** (logos) se guardan como data URI base64 en columnas de texto (máx. ~300 KB por imagen). Más adelante se puede pasar a Supabase Storage o S3 sin tocar el resto.

## Despliegue en Vercel

1. Importa este repo como proyecto nuevo en Vercel. Vercel detecta FastAPI automáticamente desde `app/main.py` (variable `app`); no hace falta `vercel.json`.
2. Variables de entorno: `ENV=prod`, `DATABASE_URL`, `JWT_SECRET` (largo y aleatorio), `CORS_ORIGINS` (URL del front).
3. En producción `/docs` y `/openapi.json` quedan desactivados.

## Pendiente (siguientes iteraciones)
Portar del prototipo: contrato (lector PDF), asistencia, bitácora, control de personal, pagos, finanzas y EPP; límites de peticiones (rate limiting) en el login; 2FA para super admin y admin; envío de correos de invitación (hoy la clave temporal se muestra una vez en pantalla); recuperación de contraseña; tabla de sesiones para revocar tokens.
