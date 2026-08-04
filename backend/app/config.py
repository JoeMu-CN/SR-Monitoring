import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://supplier_risk:local_mvp_change_me@postgres:5432/supplier_risk",
)
