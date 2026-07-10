release: cd backend && python manage.py migrate --noinput
web: cd backend && gunicorn project.wsgi:application
worker: cd backend && celery -A project.celery worker --loglevel=info
beat: cd backend && celery -A project.celery beat --loglevel=info
