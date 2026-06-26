release: cd HealthyGatorSportsFanDjango && python manage.py migrate --noinput
web: cd HealthyGatorSportsFanDjango && gunicorn project.wsgi:application
worker: cd HealthyGatorSportsFanDjango && celery -A project.celery worker --loglevel=info
beat: cd HealthyGatorSportsFanDjango && celery -A project.celery beat --loglevel=info
