import os
from dotenv import load_dotenv

# Force load the .env file from the current directory
load_dotenv(dotenv_path=".env")

print("=== CHECKING DATABASE CREDENTIALS ===")
print("USER:", os.getenv("POSTGRES_USER"))
print("PASSWORD:", os.getenv("POSTGRES_PASSWORD"))
print("HOST:", os.getenv("POSTGRES_HOST"))
print("DB:", os.getenv("POSTGRES_DB"))
print("=====================================")