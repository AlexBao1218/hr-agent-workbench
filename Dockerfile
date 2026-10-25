FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY contracts ./contracts
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./frontend/
COPY frontend/src ./frontend/src
COPY frontend/public/micro-connect-logo.png ./frontend/public/micro-connect-logo.png
RUN cd frontend && npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HR_DATA_DIR=/data HR_FRONTEND_DIST=/app/frontend/dist \
    HR_DOCUMENTS_SOFFICE=/usr/bin/libreoffice \
    PYTHONPATH=/app/backend:/app/packages/documents
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer fonts-noto-cjk fonts-dejavu-core \
    tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/hr_backend ./backend/hr_backend
COPY packages/documents/pyproject.toml ./packages/documents/pyproject.toml
COPY packages/documents/hr_documents ./packages/documents/hr_documents
RUN pip install --no-cache-dir ./backend ./packages/documents
COPY --from=frontend /build/frontend/dist ./frontend/dist
COPY deploy/start.sh ./deploy/start.sh
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app:app /data \
    && chmod +x deploy/start.sh
USER app
EXPOSE 10000
CMD ["/app/deploy/start.sh"]
