import os

BOT_TOKEN         = os.environ["BOT_TOKEN"]
OWNER_ID          = int(os.environ["OWNER_ID"])   # The one true owner — set this in Railway env vars
REQUIRED_CHANNELS = os.environ["REQUIRED_CHANNELS"].split(",")
FILE_EXPIRY_DAYS  = int(os.environ.get("FILE_EXPIRY_DAYS", "3"))
DB_PATH           = os.environ.get("DB_PATH", "/data/file_vault.db")
