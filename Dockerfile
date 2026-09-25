# One image for web and worker. GeoDjango needs the system GDAL/GEOS/PROJ
# libraries; gdal-bin pulls all three in. Bookworm is pinned so the GDAL
# version does not move under us when python:slim changes its Debian base.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gdal-bin libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# collectstatic needs settings to import, but not a database or real secret.
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "trailgeek.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "300", \
     "--access-logfile", "-", "--error-logfile", "-"]
