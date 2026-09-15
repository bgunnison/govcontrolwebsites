# Government AI Control

Standalone source, updater, builder, tests, and deployment tooling for
<https://governmentaicontrol.com>.

## Layout

- `content/` contains tracked site metadata and images; local article JSON in `content/posts/` is excluded from GitHub.
- `update/` researches and writes source-backed article JSON.
- `build/` renders the complete static site into `dist/`.
- `test/` compiles the Python source, rebuilds, and verifies links, feeds, and sitemaps.
- `deploy/` uploads a verified build by SCP over SSH; unchanged files are skipped.
- `static/` contains shared CSS and browser JavaScript for this site.
- `site_config.py` is the single site-specific configuration file.
- `manage.py` is the command-line entry point.

## Commands

```powershell
python -m pip install -r requirements.txt
python manage.py status
python manage.py build
python manage.py test
python manage.py update
python manage.py deploy
```

The preferred entry points are the top-level `update.bat`, `view.bat`, and `deploy.bat`.
They load the ignored top-level `private.py`; copy `../private.example.py` to create it.
Deployment always runs the test suite first.
