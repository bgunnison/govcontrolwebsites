# Government Control sites

This repository coordinates three otherwise separate static-site projects:

- `governmentaicontrol.com/`
- `governmentguncontrol.com/`
- `governmenthealthcarecontrol.com/`

Each site keeps its own configuration, updater, builder, tests, and local articles.
Root-level scripts share SSH/SCP deployment, backups, and Windows/Linux scheduling.
Repository: [bgunnison/govcontrolwebsites](https://github.com/bgunnison/govcontrolwebsites).

## Fresh clone setup

Use Windows with Python 3.11+ and Git. Enable long-path support for local article
filenames when cloning:

```powershell
git -c core.longpaths=true clone https://github.com/bgunnison/govcontrolwebsites.git
cd govcontrolwebsites
git config core.longpaths true
git config core.hooksPath .githooks
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest test_weekly_run test_backup test_ssh_deploy
.\.venv\Scripts\python.exe update_all.py --build-only
```

A fresh clone contains no posts. Building and testing works with empty archives
and does not need credentials or paid API calls. To rebuild the existing websites,
restore each site's `content/posts/` from your private local copy, along with
`content/imported_media/` and `content/wordpress-import.json` when present.
Keep a separate private backup of this content; GitHub does not store it.
Restore and verify the content before deploying from a fresh clone, since an empty
archive build would replace the published articles.
To use the Windows launchers with this virtual environment, activate it first:
`.\.venv\Scripts\Activate.ps1`.
For news updates or deployment, create the private configuration:

```powershell
Copy-Item private.example.py private.py
```

Do not overwrite an existing `private.py`. Fill in the API key, SSH authentication,
and absolute server paths locally. Set up trusted SSH host keys before deploying.
The weekly Windows task is **opt-in on each computer**; cloning the repository does
not register or start it. To use a virtual environment for the task, pass its
absolute `python.exe` path to `schedule/install_weekly.ps1 -PythonPath`.

## GitHub and commit hygiene

Commit source code, site configuration, logos, tests, and the sanitized
`private.example.py`. **Posts must never go on GitHub.** All `posts/` directories,
imported article media, and WordPress import manifests are ignored and rejected
by the repository audit. Keep `private.py`, SSH keys, `.env` files, virtual
environments, `dist/`, backups, logs, and machine-generated status files out of Git.
No publishing license has been selected.

The GitHub Actions workflow runs offline regression tests and rebuilds/verifies all
three sites with empty archives on Windows and Linux. It never generates news, connects to the web servers,
registers the Windows task, or deploys; no API/SSH secrets are needed in GitHub.

Before committing, review the staged files and run:

```powershell
python scripts/check_repository.py
git diff --cached --check
git diff --cached --stat
```

The configured pre-commit hook runs this audit before each local commit, including
when ignored files were force-added. GitHub Actions also runs the audit.
The check audits staged paths, common credential signatures, and large files.
It is a guardrail, not a guarantee that arbitrary secrets cannot be committed.

## Windows launchers

- `update.bat` researches current news for every site, writes new article JSON, then
  builds and verifies all three `dist/` directories. It pauses before closing so errors
  remain visible; scheduled jobs can use `update.bat --no-pause`. It never uploads or
  deploys website files.
- `view.bat` starts local-only preview servers and opens all three sites in Chrome at
  ports 8765, 8766, and 8767.
- `backup.bat` downloads the configured/enabled websites with SCP into a new
  `backups/YYYY-MM-DD_HH-MM-SS_microseconds-id/<website>/` folder. It does not build,
  update, deploy, or modify server files. Existing backups are never overwritten.
  Hidden files (including `.htaccess`) and subdirectories are included.
  Use `backup.bat --site governmentguncontrol.com` for one site, or add
  `--no-pause` for unattended runs.
- `deploy.bat` rebuilds and verifies each active, configured site, then publishes its `dist/`
  directory using SCP over SSH. The first deployment uses an atomic staging-directory
  swap; later deployments upload only changed files and remove only stale files
  tracked by the deployment manifest. This is the manual upload launcher; the
  weekly scheduled workflow also calls this deployment code after its checks.

## Weekly automatic update and deployment

The production schedule runs on Bluehost every **Sunday at 10 PM server local
time**, independently of the desktop. It uses a private Python 3.11 environment
and a source/content directory outside the public web roots. `private.py` is
owner-readable only (600), inside an owner-only directory (700). Actual posts,
credentials, notification recipients, logs, and backups are kept out of GitHub.
Failure emails go to `WEEKLY_ALERT_EMAIL` in `private.py`; successful runs are quiet.
Credentials are redacted from updater logs, the weekly log, status, and email summaries.

To reproduce the Linux setup after installing Python and restoring private content:

```sh
python -m pip install -r requirements.txt
chmod 700 .
chmod 600 private.py
python -m unittest test_weekly_run test_backup test_ssh_deploy
python update_all.py --build-only
python weekly_run.py --dry-run
python weekly_run.py --test-email
python schedule/install_linux.py --install
```

The installer preserves existing cron jobs, saves the old crontab privately in
`logs/`, and verifies the new entry without starting it. Disable the old desktop
task after validating the server installation to avoid duplicate API usage and
deployments. Server posts become the working archive; sync them privately before
using the desktop's manual deployment commands. Backups made on Bluehost share
the hosting account; retain a separate off-server copy for recovery.

Windows Task Scheduler remains an alternative: `GovControl Weekly Update and Deploy`
runs every Sunday at **10:00 PM Pacific**, following Windows daylight-saving time. Codex does not
need to be open. The task uses your existing Windows sign-in; locking the screen
is fine, but signing out prevents it from running until you sign in again.
It requests wake-from-sleep and runs a missed schedule when Windows is available.

The scheduled runner executes `update_all.py` (news, builds, tests), then
`backup_all.py`, then `deploy_all.py`. A failure stops subsequent steps. Research
and summary errors also block deployment even if the updater exits successfully.
SCP uploads are limited to the active, enabled sites in the existing configuration.
If deployment fails halfway through, already uploaded files are not automatically
rolled back; consult the log and the pre-deployment backup.

Each run retains a dated log and JSON result in `logs/weekly/`.
`WEEKLY_STATUS.txt` and `logs/weekly/latest-status.json` show the latest result;
run `weekly_status.bat` for a colored status display, next scheduled run, Windows
task result, and recent log output. Failures create a persistent desktop
`GovControl Weekly Status.txt` notice and send a Windows notification. If toast
notifications are disabled, a short popup is used instead without changing your
notification settings. The desktop notice is marked resolved after a successful
run. Logs/status never copy credentials from
`private.py`; generated status/logs/backups are ignored by Git.

There is at most one scheduled attempt per Sunday-to-Sunday period, even after
failure. Overlapping runs and automatic failure retries are disabled to avoid
repeated API spend or deployments. After investigating a failure, you can still
run the existing manual commands. Each stage has a three-hour timeout.
The existing configured OpenAI update cost limit still applies.

To install/reinstall the task without starting it:
`powershell -NoProfile -ExecutionPolicy Bypass -File schedule/install_weekly.ps1`.
To safely inspect the sequence without network calls:
`python weekly_run.py --dry-run`.
Offline tests: `python -m unittest test_weekly_run`.

SCP copies both website files and the deployment manifest. SFTP is used only for
remote metadata, directory operations, and renames, not uploads. Transfers reuse
the SSH authentication and host-key verification configured in `private.py`.
Install deployment dependencies with `python -m pip install -r requirements.txt`;
run offline deployment checks with `python -m unittest test_ssh_deploy`.
The three site paths must be full absolute server paths visible to SSH (often
`/home/account/public_html/site`), not FTP-root-relative paths such as `/public_html/site`.
The parent directory is checked before any upload.

Backups use the same SSH credentials and site paths as deployment, regardless of
whether the local site's `active` flag is set. Each run contains `backup.json`
with completion status plus per-site manifests containing file sizes, SHA-256
checksums, and remote modification times/permissions. File lists/sizes are verified
and the source is checked for changes before a download is marked complete.
Failed/interrupted downloads stay in `<website>.partial`; other sites continue
after a failure. Symbolic links, special files, and Windows-incompatible filenames
produce an explicit error rather than an incomplete backup marked successful.

`backups/` is ignored by Git: backups may contain passwords in server configuration
files such as `wp-config.php`. Keep this folder private. These are file backups,
not database exports; a full WordPress recovery also needs its database backup.
Run offline backup tests with `python -m unittest test_backup`.

Copy `private.example.py` to `private.py`, fill in the OpenAI key, `SSH`, authentication,
and the three absolute document-root paths. `private.py` is ignored and must never be
committed. SSH host keys are verified through the normal `~/.ssh/known_hosts` file.

The updater processes sites and topics sequentially. It skips topics already completed
that day, honors OpenAI rate-limit reset headers, retries temporary throttling with
bounded backoff, and saves unfinished source research for the next run. If a limit is
still active, wait for the API window to reset and run `update.bat` again; completed
topics are not repeated.

Publication dates are bounded by the local machine's current date. Research must use
the source's actual publication date, not an event date, deadline, or date in a URL.
Unknown, invalid, and future source dates are excluded; saved research is checked
again when resumed. Summaries cannot replace approved source metadata. New posts
retain source publication dates and a separate local `created_at` date. The build
and test steps reject future publication metadata before replacing `dist/`.

On September 14, 2026, eight event-related posts (five gun-control and three healthcare)
were corrected to their logged local creation date because their source publication
dates were unverified. Their JSON records retain the previous mistaken dates in
`date_correction`, with `published_at_basis: "site_created"`; article URLs and event
details are unchanged. Date regression tests use mocked research and make no paid API calls.

Each API response records input, cached-input, output, and web-search usage in the site
generation log and in a per-run ledger under `logs/`. `update.bat` prints the estimated
total cost. It uses a shared $5 estimated-cost stop by default; override it with
`OPENAI_MAX_UPDATE_COST_USD` in `private.py` or the environment. This local estimate is
a safety stop, not a substitute for an OpenAI project hard spend limit. Web research
uses the balanced `medium` search context to control token cost.
