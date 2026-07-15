# Archived PyInstaller Specs

This folder contains historical Windows packaging specs that are no longer part
of the release build.

`drtransition-backend.spec.deprecated` packaged the old local FastAPI/backend
bundle with schema, seeds, and database-facing code. The current Windows
installer must not bundle backend APIs, migrations, seeds, MySQL assets, or
database scripts. Backend deployment is hosted separately.

The active PyInstaller specs are still one directory up:

- `drtransition-reranker.spec`
- `drtransition-nli.spec`

Those build local desktop companion services only.
