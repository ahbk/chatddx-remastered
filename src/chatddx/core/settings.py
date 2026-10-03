import os
from pathlib import Path

from psycopg.conninfo import make_conninfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def database(admin: bool = False) -> str:
    return make_conninfo(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_ADMIN" if admin else "DB_USER"],
        dbname=os.environ["DB_NAME"],
    )
