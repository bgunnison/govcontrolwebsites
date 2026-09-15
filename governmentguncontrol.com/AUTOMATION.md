# Automation contract

The top-level Windows scheduled task runs `weekly_run.py --scheduled` once per week,
Sunday at 10 PM Pacific: update/build/test, verified server backup, then SCP deployment.
Do not schedule a second per-site job. See `../README.md` for installation and status.

`import-wordpress` is a migration tool, not part of the weekly schedule. File backups
do not replace a WordPress database export.

Update, test, or backup failures block deployment. Deployment errors can leave
files/sites already uploaded; preserve the backup and log and report partial progress.
Manual `update.bat` still never uploads files.
