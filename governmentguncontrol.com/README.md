# Government Gun Control

Standalone source, updater, builder, tests, and deployment tooling for
<https://www.governmentguncontrol.com>.

The recovered WordPress archive is stored locally in `content/posts/` and
`content/imported_media/`, with its manifest in `content/wordpress-import.json`.
These files are excluded from GitHub; a fresh clone has an empty archive.
Article slugs remain root-level so existing public URLs continue to work after
the static site replaces WordPress.

## Layout

- `content/` contains tracked site metadata and branding, plus ignored local articles and imported media.
- `update/` contains both the ongoing article updater and the WordPress importer.
- `build/` renders the complete static site into `dist/`.
- `test/` compiles the Python source, rebuilds, and verifies links, feeds, and sitemaps.
- `deploy/` uploads a verified build by SCP over SSH; unchanged files are skipped.
- `static/` contains CSS and browser JavaScript for this site.
- `site_config.py` is the single site-specific configuration file.
- `manage.py` is the command-line entry point.

## Commands

```powershell
python -m pip install -r requirements.txt
python manage.py status
python manage.py build
python manage.py test
python manage.py update
python manage.py import-wordpress
python manage.py deploy
```

`import-wordpress` refreshes the archive from the configured public WordPress API and
can accept `--source https://example.com`. The preferred entry points are the top-level
`update.bat`, `view.bat`, and `deploy.bat`. They load the ignored top-level `private.py`;
copy `../private.example.py` to create it. Deployment always runs the test suite first.
