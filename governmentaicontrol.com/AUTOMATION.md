# Automation contract

The top-level Windows scheduled task runs `weekly_run.py --scheduled` once per week,
Sunday at 10 PM Pacific: update/build/test, verified server backup, then SCP deployment.
Do not schedule a second per-site job. See `../README.md` for installation and status.

Update, test, or backup failures block deployment. Deployment errors stop remaining
work but can leave files/sites already uploaded; report the failure and preserve the
backup and log. Do not claim that a failed deployment automatically rolled back.
Manual `update.bat` still never uploads files.
