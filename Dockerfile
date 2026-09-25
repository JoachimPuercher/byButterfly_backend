FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=core.settings.prod

# ffmpeg is needed by yt-dlp when a downloaded stream has to be remuxed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Not root: this image also runs the worker, which feeds third-party audio
# to ffmpeg and yt-dlp. A parser bug must not hand out root in the container.
# The app directory is owned by the user so collectstatic and the audio
# directory (JENYMIA_AUDIO_DIR, under /app) can be written at runtime.
RUN useradd --create-home --uid 10001 app
COPY --chown=app:app . .
USER app

# The worker downloads the whisper weights into this directory on first use.
# It has to exist in the image and belong to app: a named volume mounted on a
# path the image does not have is created as root, and app could not write
# to it. With the directory present, Docker seeds the volume from it, owner
# included.
RUN mkdir -p /home/app/.cache/huggingface

# Everything that needs the database or the settings happens at container
# start, not at build time: settings require SECRET_KEY and POSTGRES_* at
# import, which the build has no access to. WhiteNoise then serves the static
# files from STATIC_ROOT.
#
# Migrations run per app and never as a bare "migrate": each app owns its own
# migrations and its own tables, which is what makes moving a single app out
# later possible. Django's own apps come first because accounts and jenymia
# depend on them.
CMD ["sh", "-c", "python manage.py migrate contenttypes --noinput && python manage.py migrate auth --noinput && python manage.py migrate admin --noinput && python manage.py migrate sessions --noinput && python manage.py migrate accounts --noinput && python manage.py migrate jenymia --noinput && python manage.py collectstatic --noinput && gunicorn core.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2"]
