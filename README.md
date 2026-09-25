# byButterfly Backend

Django / DRF / PostgreSQL backend for all byButterfly products.

It is a **modular monolith**: one Django app per product, one shared user model, one
database, hard boundaries between the apps. Frontends live in their own repositories
(jenymia: Next.js, apps: React Native) and talk to this backend only through the REST API.

**Phase 1 (current):** only `apps/jenymia` — the product analysis pipeline plus read
endpoints. No login yet. `accounts` and `common` exist from day one so that adding
authentication later does not mean restructuring.

---

## Layout

```
core/settings/{base,dev,prod}.py   no business logic, only INSTALLED_APPS and include()
apps/common/                        abstract base models, pagination, exception handler
apps/accounts/                      user model (minimal), later JWT/OAuth
apps/jenymia/
  models/                           product, lookups, details, analysis orders
  services.py                       every write goes through here
  selectors.py                      every read goes through here
  pipeline_shared/                  fetch, prompt, parse - runs anywhere
  pipeline_local/                   yt-dlp + faster-whisper - bound to this machine
  api/v1/                           serializers, views, urls, throttling
  admin.py
  migrations/
```

### Rules that are not negotiable

These keep a single app extractable into its own service later.

- **No import and no ForeignKey between product apps.** Allowed: `apps.common`,
  `settings.AUTH_USER_MODEL`, and `apps.<other>.services`. Origin from another app is a
  string field (`product="jenymia"`), never a foreign key.
- **Views call `services/` to write and `selectors.py` to read** — never `Model.objects`
  directly. The pipeline writes only through services.
- **Every model inherits `apps.common.models.BaseModel`** (UUID primary key, `created_at`,
  `updated_at`).
- **Never run `makemigrations` or `migrate` without an app name.** Each app owns its own
  migrations and its own tables. The only exception is `makemigrations --check --dry-run`,
  which creates nothing.
- Money is `DecimalField`. No `null=True` on text fields. Translations live in
  `<Model>Translation` with `UniqueConstraint(["<model>", "locale"])`.
- Constraints belong in the database (`UniqueConstraint`, `CheckConstraint`,
  `Meta.indexes`). `on_delete` is chosen deliberately: `PROTECT` for master data.
- Every setting comes from `os.environ` with no default. A missing variable must stop the
  process at startup, not surface later as wrong behaviour.
- Code comments and docstrings are English. The prompt files under
  `pipeline_shared/prompts/` are German, because that is the language the model answers in.

---

## Requirements

- Python 3.13
- PostgreSQL 17
- Redis 7 (queue broker, only where a worker runs)
- ffmpeg (yt-dlp remuxes downloaded audio with it — already in the Docker image)
- Docker Desktop for the local database, Redis and worker

---

## Local setup

```bash
cp .env.example .env          # then fill in the values, see below
docker compose up --build     # db, redis, web, worker
```

Migrations are **not** run by Compose. Run them from your own virtualenv against the
database in the `db` container, one app at a time:

```bash
python manage.py migrate accounts
python manage.py migrate jenymia
```

> `POSTGRES_HOST=db` only resolves inside Compose. When you run `manage.py` from the host,
> set `POSTGRES_HOST=localhost` in your `.env`.

The `web` container creates the superuser on start from `DJANGO_SUPERUSER_EMAIL` and
`DJANGO_SUPERUSER_PASSWORD`. Login is by e-mail address — `accounts.User` has no username.

Admin: http://localhost:8000/admin/ · Queue dashboard: http://localhost:8000/django-rq/

### Running against the production database

A second env file, `.env.railway`, points `POSTGRES_*` at the Railway TCP proxy. Start
without the `db` service:

```bash
docker compose --env-file .env.railway up web worker redis
```

Both env files are gitignored and both must define `ENV_FILE` with their own name:
`--env-file` only fills the `${...}` placeholders in `docker-compose.yml`, while `env_file`
hands the same file to the containers.

---

## Environment variables

Every one of these is required — there are no defaults in the settings.

| Variable | Notes |
|---|---|
| `DJANGO_SETTINGS_MODULE` | `core.settings.dev` or `core.settings.prod` |
| `SECRET_KEY` | |
| `POSTGRES_DB` `POSTGRES_USER` `POSTGRES_PASSWORD` `POSTGRES_HOST` `POSTGRES_PORT` | no `DATABASE_URL` |
| `POSTGRES_SSLMODE` | `prefer` inside Docker and on Railway's private network, `require` through the public TCP proxy |
| `DEPLOY_BACKGROUND_WORKERS` | `True` where a worker and Redis exist, `False` on Railway in phase 1 |
| `JENYMIA_MEDIA_BASE_URL` | the domain that relative image keys hang from |
| `DJANGO_SUPERUSER_EMAIL` `DJANGO_SUPERUSER_PASSWORD` | used by `createsuperuser --noinput` at container start |

Only when `DEPLOY_BACKGROUND_WORKERS` is true:

| Variable | Notes |
|---|---|
| `REDIS_HOST` `REDIS_PORT` `REDIS_DB` `REDIS_PASSWORD` | |
| `RQ_DEFAULT_TIMEOUT` `RQ_RESULT_TTL` | seconds |
| `JENYMIA_AUDIO_DIR` | scratch space between download and transcript; the file is deleted once its text is stored |
| `MAX_VIDEO_DURATION_SECONDS` | checked before the download, not after |
| `INGEST_MAX_ATTEMPTS` | total runs including the first; must be at least 2 |
| `WHISPER_MODEL` `WHISPER_DEVICE` `WHISPER_COMPUTE_TYPE` | |
| `GEMINI_API_KEY` `GEMINI_MODEL` `ANTHROPIC_API_KEY` `ANTHROPIC_MODEL` | Gemini answers first, Claude takes over when its quota is spent |

Production only (`core.settings.prod`):

| Variable | Notes |
|---|---|
| `ALLOWED_HOSTS` | comma separated |
| `CONN_MAX_AGE` | seconds; gunicorn has no pooler in front of it |
| `LOG_LEVEL` | |

Secrets never go into code or git. Keep `.env.example` in sync when you add a variable.

---

## The jenymia analysis pipeline

An order (`ProductToAnalyse`) is entered in the admin: title, brand, a category and a
handful of YouTube and web URLs. The category decides which prompt runs — it is chosen by
hand and never guessed. Two queue jobs turn the order into a product.

```
admin saves order
  -> services.request_analysis   status: queued
  -> run_ingest                  status: running
       yt-dlp + faster-whisper for YouTube, trafilatura for web pages
       raw text and metadata are stored per source
  -> services.request_extract    status: text_extracted
  -> run_extract
       build prompt from prompts/_base.md + prompts/<pipeline>.md
       ask Gemini, fall back to Claude
       validate the answer against pipeline_shared/schema.py
       write the product and all its rows
                                 status: analyse_complete
```

Anything that fails for good sets `status = failed` and writes the reason into
`ProductToAnalyse.error`, which the admin shows. Transient failures (timeout, 503, rate
limit) raise `IngestError`, and rq retries the job up to `INGEST_MAX_ATTEMPTS`. Sources
that already produced text are skipped on a retry, so only what actually failed runs again.

Two things are deliberately kept apart:

- **`pipeline_shared/`** runs anywhere. **`pipeline_local/`** does not: YouTube blocks
  datacenter addresses and Railway has no GPU for whisper. A server needs another
  implementation of `youtube_text(url) -> (text, language, metadata)` — one import changes
  then, nothing else.
- **Ingest and extract are separate jobs** so the analysis can be repeated after a prompt
  fix without downloading and transcribing everything again. The raw text is already in
  the database.

### The answer contract

`pipeline_shared/schema.py` is the single definition of what the model has to return. It is
used three times: as the JSON schema handed to the provider, as the validation of the
answer, and as the coercion of the values models get wrong in predictable ways. Field names
are column names. Change it together with the prompt, never one alone.

Three pipelines exist, and the value is also the prompt filename:
`toys` → `prompts/toys.md`, `school`, `tech`.

Nothing from the answer reaches a model constructor unfiltered: `services._clean()` drops
keys that are not columns and cuts strings to the column length.

### What the analysis does not decide

- The **public source list** is built from the order's own URLs, never from the answer — a
  citation the model invented would be worse than none.
- The **main category** is set on the order.
- **Images and affiliate links** are entered by hand.
- **Publication.** The pipeline always writes `is_published=False`. A product goes live
  only through `services.publish_product()`, which checks that a traffic-light score and
  both translations exist.

---

## API

```
GET /api/v1/jenymia/products/<locale>/<slug>/
```

`locale` is `de` or `en`; an unknown one is a 404 rather than a silent fall back to German.
Unpublished products answer exactly like typos — a draft must not be discoverable by
probing slugs.

Conventions: one serializer per use case, never `fields="__all__"`.
`permission_classes` is set explicitly on every view. Lists are paginated and use
`select_related`/`prefetch_related`. Inside `v1` fields may be added, never renamed or
removed; an incompatible shape becomes `v2` next to it.

Authentication is session only. DRF's default would also enable HTTP Basic, which would let
anyone try staff passwords against every endpoint — and authentication runs before
throttling, so the rate limit would not stop it.

---

## Validating a change

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
ruff check .
lint-imports
```

> `pytest` and `import-linter` are not installed yet and there is no `pyproject.toml`, so
> the last two steps cannot run today. Until that is set up, the first three are the bar.

---

## Deployment

- **Backend and PostgreSQL:** Railway. **jenymia frontend:** Vercel. **Local:** Docker
  Compose (`web` from the Dockerfile, `db` = `postgres:17`).
- Railway terminates TLS and forwards plain HTTP to gunicorn. `core/settings/prod.py`
  turns that back into enforced HTTPS: redirect, secure cookies, HSTS,
  `SECURE_PROXY_SSL_HEADER`.
- The container migrates each app on start, then collects static files, then starts
  gunicorn. Static files are served by WhiteNoise, not from object storage.
- **Images:** Cloudflare R2, one bucket per product app (`bybutterfly-<app>`). The database
  stores relative keys only, never absolute URLs — the domain belongs to the environment.
- **Phase 1 worker split:** the analysis runs on a local machine against the Railway
  database, because of the YouTube and whisper constraints above. Railway therefore runs
  with `DEPLOY_BACKGROUND_WORKERS=False` and has no broker.

---

## Current state

Working: models, admin for orders and products, both pipeline jobs, the read endpoint.

Known gaps, in the order they matter:

- Railway cannot hand an order to the local worker. With `DEPLOY_BACKGROUND_WORKERS=False`
  nothing is enqueued and the order stays `queued`; the worker only reads from Redis.
- `docker-compose.yml` pins `DJANGO_SETTINGS_MODULE` to `core.settings.dev` in the
  `environment:` block, which overrides the env file. Fix that before the first run against
  the production database, or you get `DEBUG=True` against production data.
- No CORS configuration and no health endpoint, both needed before the Vercel frontend and
  Railway's health check can work. `django-cors-headers`, `django-filter`, `django-redis`
  and `drf-spectacular` are installed but not wired up.
- Throttle counters live in `LocMemCache`, so they count per process and reset on restart.
- Several values from a language model can still abort a whole analysis: a duplicate spec
  key, a sub-category whose English slug is already taken, a product slug over 160
  characters. `manufactured_in_country` is truncated rather than validated, so `"China"`
  becomes `CH` — Switzerland.

A full fix plan with severities and a verification checklist lives outside the repository;
ask the maintainer for it.

---

## Further reading

Files under `.claude/` are not loaded automatically — read them when the task calls for it.

| File | When |
|---|---|
| `.claude/shared/butterfly-architekture.md` | architecture decisions, the playbook for extracting an app |
| `.claude/shared/BF_CODE_BACKEND.md` | detailed Django/DRF/Postgres conventions |
| `.claude/product-data-fields.md` | jenymia fields, when working on the schema |
| `.claude/backlog.md` | roadmap: which step is next and what "done" means |
| `.claude/db-integration.md` | state of the database work |
| `.claude/shared/BF_CORE.md`, `BF_PROJECTS.md` | brand, products, voice — for content tasks |
