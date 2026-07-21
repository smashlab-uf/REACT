from __future__ import absolute_import, unicode_literals

import os
import ssl

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
app = Celery('project')

app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

broker_url = os.environ.get('CELERY_BROKER_URL') or os.environ.get('REDIS_URL')
result_backend = os.environ.get('CELERY_RESULT_BACKEND') or broker_url

if broker_url:
    app.conf.broker_url = broker_url

if result_backend:
    app.conf.result_backend = result_backend

if broker_url and broker_url.startswith('rediss://'):
    app.conf.broker_use_ssl = {'ssl_cert_reqs': ssl.CERT_NONE}

if result_backend and result_backend.startswith('rediss://'):
    app.conf.redis_backend_use_ssl = {'ssl_cert_reqs': ssl.CERT_NONE}


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')