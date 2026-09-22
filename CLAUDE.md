# byButterfly Backend

Ein Django/DRF/PostgreSQL-Backend für alle byButterfly-Produkte. **Modularer Monolith:** eine Django-App pro Produkt, gemeinsames User-Model, eine Datenbank, harte App-Grenzen. Frontends sind getrennte Repos (jenymia: Next.js · Apps: React Native) und sprechen nur über die REST-API.

**Phase 1 (jetzt):** nur `apps/jenymia` — Produktanalyse-Pipeline + Read-Endpoints. Kein Login. `accounts` und `common` stehen trotzdem von Tag 1.

## Struktur

```
core/settings/{base,dev,prod}.py   keine Business-Logik, nur INSTALLED_APPS + include()
apps/common/                        abstrakte Basis-Models, Pagination, Exception-Handler — keine eigenen Tabellen
apps/accounts/                      User-Model (minimal), später JWT/OAuth
apps/<produkt>/                     Domäne: models/ services/ selectors.py pipeline/ admin.py migrations/ tests/
apps/<produkt>/api/v1/              Transport: serializers.py views.py filters.py permissions.py urls.py
```

## Regeln (verbindlich)

**Grenzen**
- Kein Import und kein ForeignKey zwischen Produkt-Apps. Erlaubt: `apps.common`, `settings.AUTH_USER_MODEL`, `apps.<andere>.services`.
- `services/` ist die öffentliche Schnittstelle einer App. Herkunft anderer Apps als String-Feld (`product="jenymia"`), nie FK.
- Views rufen nur `services/` (schreiben) und `selectors.py` (lesen) — nie `Model.objects` direkt. Pipeline schreibt nur über Services.
- `common` und `accounts` kennen keine Produkt-Apps.

**User**
- `accounts.User`: UUID-PK, E-Mail-Login, `display_name`, `is_staff`, `is_active` — sonst nichts. Produkt-Wissen → `<Produkt>Profile` in der Produkt-App.
- User immer über `settings.AUTH_USER_MODEL` referenzieren.

**Datenbank**
- Alle Models erben `apps.common.models.BaseModel` (UUID, `created_at`, `updated_at`).
- `makemigrations <app>` immer mit App-Namen. Jede Migration reversibel; Daten-Migrationen getrennt.
- Constraints in der DB (`UniqueConstraint`, `CheckConstraint`, `Meta.indexes`). `on_delete` bewusst: `PROTECT` für Stammdaten.
- Geld `DecimalField`. Kein `null=True` auf Text. Übersetzungen als `<Model>Translation` mit `UniqueConstraint(["<model>", "locale"])`.
- Kein Raw-SQL ohne gebundene Parameter.

**API**
- `/api/v1/<app>/<resource>/`, Router pro App. `permission_classes` explizit auf jedem View. `get_queryset()` filtert (published / User).
- Ein Serializer pro Use-Case, nie `fields="__all__"`. Listen paginiert, `select_related`/`prefetch_related`.

**Secrets / Settings**
- Alles aus Env (`django-environ`). Nie Secrets in Code oder Git. `.env.example` pflegen.

**Code**
- ruff (Format + Lint), Type-Hints an öffentlichen Funktionen, `logging` statt `print`, keine Emojis im Code, Business-Logik in `services/`.
- Charity-Quote überall **5 %**.

## Validierung nach jeder Änderung

```
python manage.py check && python manage.py makemigrations --check --dry-run && pytest && ruff check . && lint-imports
```

## Commit

Bei der Eingabe von `commit` (auch „commit das", „commit & push" o. ä.):

1. Validierung oben ausführen — bei Fehlern **nicht** committen, sondern melden.
2. `git status` / `git diff` prüfen, nur relevante Dateien stagen (keine `.env`, keine Secrets).
3. Commit-Message nach **Conventional Commits**:
   - Format: `<type>(<scope>): <subject>` — Typen: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `chore`, `build`, `ci`, `style`.
   - Scope = App-Name (`jenymia`, `accounts`, `common`, `core`) oder weglassen.
   - Subject: Englisch, Imperativ, Kleinbuchstabe am Anfang, kein Punkt, max. 72 Zeichen.
   - Body nur wenn nötig (Was/Warum, nicht Wie). Breaking Changes als `BREAKING CHANGE:` Footer oder `!` nach dem Typ.
   - Mehrere unabhängige Änderungen → mehrere Commits, nicht ein Sammel-Commit.
4. **Kein `Co-Authored-By`, kein „Generated with"-Hinweis** — keinerlei KI-Attribution in Message oder Body.
5. Danach direkt `git push` auf den aktuellen Branch (Upstream setzen, falls nicht vorhanden). Kein `--force`.

## Nur bei Bedarf lesen (nicht auto-laden)

| Datei | Wann |
|---|---|
| `.claude/shared/butterfly-architekture.md` | Architektur-Entscheidungen, Herauslös-Playbook, Begründungen |
| `.claude/shared/BF_CODE_BACKEND.md` | Detail-Konventionen Django/DRF/Postgres |
| `.claude/product-data-fields.md` | jenymia-Felder beim Schema-Bau |
| `.claude/backlog.md` | Roadmap — welcher Schritt ist dran, was ist „fertig wenn" |
| `.claude/db-integration.md` | DB-Arbeitsstand |
| `.claude/shared/BF_CORE.md` · `BF_PROJECTS.md` | Marke, Produkte, Voice — bei Content/Text-Aufgaben |
| `.claude/README.md` | wie dieser Ordner funktioniert |
| `D:\analyse\modular_monolith.pdf` | Ausführliches Infoblatt zur Architektur |

Modi: `/learn-mode` (Nutzer baut, Claude erklärt kurz) · `/build-mode` (Claude baut). Agents nur auf Anfrage.
