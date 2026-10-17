"""
Django settings for the OpenWeather dashboard.

Every secret and deployment-specific value comes from environment variables
(or the .env file next to manage.py). See .env.example.
"""
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


def env_int(name, default):
    try:
        return int(os.getenv(name, ''))
    except ValueError:
        return default


# --- Core security -----------------------------------------------------------
DEBUG = env_bool('DJANGO_DEBUG', False)
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == 'test'

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG or RUNNING_TESTS:
        # Throwaway key for local development only; it changes on every restart.
        SECRET_KEY = get_random_secret_key()
    else:
        raise ImproperlyConfigured(
            'Set DJANGO_SECRET_KEY in your environment or .env file (see .env.example).'
        )

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]' if DEBUG else '')
if RUNNING_TESTS:
    ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']

# Behind HTTPS (the default whenever DEBUG is off) cookies are secure and HTTP redirects to HTTPS.
# Set DJANGO_HTTPS=false to run a non-debug server over plain HTTP, for example on localhost.
HTTPS = env_bool('DJANGO_HTTPS', not (DEBUG or RUNNING_TESTS))
SECURE_SSL_REDIRECT = HTTPS
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
SECURE_HSTS_SECONDS = 31536000 if HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = HTTPS
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'


# --- OpenWeather ----------------------------------------------------------------
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY', '').strip()
OPENWEATHER_BASE_URL = os.getenv('OPENWEATHER_BASE_URL', 'https://api.openweathermap.org/data/2.5')
OPENWEATHER_TIMEOUT = (5, 8)  # connect, read (seconds)
# City shown on first load. Leave empty to start with the search prompt instead.
DEFAULT_CITY = os.getenv('DEFAULT_CITY', 'Puerto Princesa').strip()
# OpenWeather refreshes current data about every 10 minutes, so caching for that long is safe.
WEATHER_CACHE_SECONDS = max(0, env_int('WEATHER_CACHE_SECONDS', 600))

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'openweather-cache',
    }
}


# --- Application definition --------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'weather',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'projectsite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'projectsite.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Manila'
USE_I18N = True
USE_TZ = True

# Static files. With DEBUG on, runserver serves them. In production run `collectstatic`
# and let your web server serve STATIC_ROOT.
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
