FROM node:24-alpine AS frontend-build

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm test
RUN npm run lint
RUN npm run build

FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple ".[dev]"

COPY backend/alembic.ini ./alembic.ini
COPY backend/alembic ./alembic
COPY backend/tests ./tests
COPY --from=frontend-build /frontend/dist ./app/static

EXPOSE 8080

CMD ["sh", "-c", "alembic upgrade ${ALEMBIC_UPGRADE_TARGET:-head} && uvicorn app.main:app --host 0.0.0.0 --port 8080"]
