FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY . .
RUN SECRET_KEY=build-only-not-used-at-runtime DATABASE_URL=sqlite:////tmp/build.sqlite3 python manage.py collectstatic --noinput
RUN useradd -m app && mkdir -p /app/private /app/staticfiles && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
