# Copy this file to private.py. Never commit private.py.

# Used by update.bat. You may set OPENAI_API_KEY in the environment instead.
OPENAI_API_KEY = ""

# Linux/Bluehost weekly failures only. Keep the actual recipient in private.py.
WEEKLY_ALERT_EMAIL = ""

# Local estimated-cost stop for one update.bat run. Configure the OpenAI project
# budget as the authoritative billing safeguard.
OPENAI_MAX_UPDATE_COST_USD = 5.00

# SCP deployment over SSH. SSH uses the standard username@hostname form. Leave the
# password empty when authenticating through an SSH key or ssh-agent.
SSH = "username@example.com"
SSH_PASSWORD = ""
SSH_PORT = 22
SSH_KEY_FILE = ""

# Host keys are checked against the user's normal ~/.ssh/known_hosts file.
# Optionally name another known-hosts file. Keeping ALLOW_UNKNOWN_HOST False
# prevents a man-in-the-middle connection to a server whose key is not trusted.
SSH_KNOWN_HOSTS = ""
SSH_ALLOW_UNKNOWN_HOST = False
SSH_TIMEOUT = 60

# The first deployment replaces each document-root directory through an atomic
# staging-directory swap. Later deployments upload only new or changed files.
SSH_INITIAL_ATOMIC = True
SSH_DRY_RUN = False

# Each value must be the full absolute SSH path to that domain's document root,
# including the account's home directory (not an FTP-root-relative path).
# Fill these in only in your ignored private.py file.
AI_PATH = ""
GUN_PATH = ""
HEALTHCARE_PATH = ""
