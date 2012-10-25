""":mod:`asuka.services.wsgi` --- WSGI server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

It supports the following servers through `Green Unicorn`_:

``sync`` (default)
   It should handle most 'normal' types of workloads.
   You'll want to read http://gunicorn.org/design.html for information
   on when you might want to choose one of the other worker classes.

``eventlet``
   Eventlet_ is a concurrent networking library for Python that
   allows you to change how you run your code, not how you write it.

``gevent``
   gevent_ is a coroutine_-based Python networking library thaat
   uses greenlet_ to provide a high-level synchronous API on top
   of the libevent_ event loop.

``tornado``
   Tornado_ is an open source version of the scalable,
   non-blocking web server and tools that power FriendFeed.

``meinheld``
   Meinheld_ is a high-performance WSGI-compliant web server that
   takes advantage of greenlet_ and picoev_ to enable asynchronous
   network I/O in a light-weight manner.

.. _Green Unicorn: http://gunicorn.org/
.. _Eventlet: http://eventlet.net/
.. _gevent: http://www.gevent.org/
.. _coroutine: http://en.wikipedia.org/wiki/Coroutine
.. _greenlet: http://codespeak.net/py/0.9.2/greenlet.html
.. _libevent: http://monkey.org/~provos/libevent/
.. _Tornado: http://www.tornadoweb.org/
.. _Meinheld: http://meinheld.org/
.. _picoev: http://developer.cybozu.co.jp/kazuho/2009/08/picoev-a-tiny-e.html

"""
import pipes

from ..service import Service

__all__ = ('EventletWorker', 'GeventWorker', 'GunicornService',
           'MeinheldWorker', 'SyncWorker', 'TornadoWorker', 'Worker')


class GunicornService(Service):

    def __init__(self, *args, **kwargs):
        super(GunicornService, self).__init__(*args, **kwargs)
        self.worker = WORKERS[self.config['server']]()

    @property
    def required_apt_packages(self):
        packages = set(super(GunicornService, self).required_apt_packages)
        packages.update(['python-setproctitle', 'gunicorn'])
        packages |= self.worker.required_apt_packages
        return packages

    @property
    def required_python_packages(self):
        packages = set(super(GunicornService, self).required_python_packages)
        packages.update(self.worker.required_python_packages)
        if self.auth_required:
            packages.add('Werkzeug')
        return packages

    @property
    def wsgi_app(self):
        if 'wsgi_script' in self.config or self.auth_required:
            return 'web_wsgi:application'
        return self.config['wsgi_app']

    @property
    def wsgi_script(self):
        wsgi_script = self.config.get('wsgi_script')
        if not self.auth_required:
            return wsgi_script
        if wsgi_script is None:
            imp = self.config['wsgi_app'].split(':')
            wsgi_script = 'from {0} import {1} as application'.format(*imp)
        appended_script = '''
import datetime
import hashlib
import hmac
import werkzeug.urls
import werkzeug.wrappers

@werkzeug.wrappers.BaseRequest.application
def auth_application(request):
    environ = request.environ
    if (environ.get('HTTP_USER_AGENT', '').startswith('ELB-HealthChecker/') and
        'X-Forwarded-For' not in request.headers and
        'X-Forwarded-Port' not in request.headers and
        'X-Forwarded-Proto' not in request.headers):
        return werkzeug.wrappers.BaseResponse(
            ['ELB Pong'],
            status=200,
            mimetype='text/plain'
        )
    auth = request.cookies.get('asuka_auth')
    sig = request.cookies.get('asuka_sig')
    if auth and sig:
        secret = {secret!r}
        if sig == hmac.new(secret, auth, hashlib.sha256).hexdigest():
            try:
                auth = datetime.datetime.strptime(auth, '%Y%m%d%H%M%S')
            except ValueError:
                pass
            else:
                if datetime.datetime.utcnow() <= auth:
                    return auth_application.application
    token = request.args.get('token')
    sig = request.args.get('sig')
    if token and sig:
        secret = {consistent_secret!r}
        if sig == hmac.new(secret, token, hashlib.sha256).hexdigest():
            try:
                ts, login, host = token.split('/', 2)
                ts = datetime.datetime.strptime(ts, '%Y%m%d%H%M%S')
            except ValueError:
                pass
            else:
                if host == request.host:
                    gap = datetime.datetime.utcnow() - ts
                    if gap <= datetime.timedelta(minutes=1):
                        back = request.cookies.get('asuka_auth_back',
                                                   request.url)
                        expires = datetime.timedelta(seconds={auth_expires!r})
                        auth_ts = datetime.datetime.utcnow() + expires
                        auth = auth_ts.strftime('%Y%m%d%H%M%S')
                        sig = hmac.new({secret!r}, auth, hashlib.sha256)
                        response = werkzeug.wrappers.Response(
                            ['Authenticated; redirecting to ', back],
                            status=302,
                            headers=dict(Location=back),
                            mimetype='text/plain'
                        )
                        response.delete_cookie('asuka_auth_back')
                        response.set_cookie('asuka_auth', auth,
                                            expires=auth_ts)
                        response.set_cookie('asuka_sig', sig.hexdigest(),
                                            expires=auth_ts)
                        return response
    delegate_url = {delegate_url!r} + '?' + werkzeug.urls.url_encode(
        dict(back=request.url)
    )
    response = werkzeug.wrappers.BaseResponse(
        ['Redirecting to ', delegate_url],
        status=302,
        headers=dict(Location=delegate_url),
        mimetype='text/plain'
    )
    response.set_cookie('asuka_auth_back', request.url)
    return response
auth_application.application = application
application = auth_application
'''
        secret = '.'.join((
            self.app.name,
            self.branch.label,
            self.app.consistent_secret
        ))
        wsgi_script += appended_script.format(
            secret=secret,
            consistent_secret=self.app.consistent_secret,
            delegate_url=self.app.url_base + '/delegate/',
            auth_expires=self.config.get('auth_expires', 3 * 3600)
        )
        return wsgi_script

    @property
    def auth_required(self):
        return bool(self.config.get('auth_required'))

    def install(self, instance):
        super(GunicornService, self).install(instance)
        format_args = {
            'service': self,
            'app_name': instance.app.name,
            'service_name': self.name,
            'service_path': instance.app.name + '/' + self.name
        }
        wsgi_script = self.wsgi_script
        if wsgi_script is not None:
            instance.write_file(
                '/etc/{service_path}/web_wsgi.py'.format(**format_args),
                wsgi_script,
                sudo=True
            )
        server_options = self.config.get('server_options', {})
        server_options.setdefault('worker_class', self.worker.worker_class)
        gunicorn_options = ' '.join(
            '--' + k.replace('_', '-')
            if v is True
            else '--' + k.replace('_', '-') + '=' + pipes.quote(str(v))
            for k, v in server_options.items()
            if v is not False and v is not None
        )
        instance.write_file(
            '/etc/init/{app_name}-{service_name}.conf'.format(**format_args),
            '''\
description "{app_name} {service_name} service"

start on runlevel [2345]
stop on runlevel [06]

env PYTHONPATH="/etc/{service_path}"

pre-start script
    mkdir -p -m0777 /var/run/{app_name} /var/log/{service_path}
    chown {app_name}:{app_name} /var/run/{app_name} /var/log/{app_name}
end script

script
    exec gunicorn --name {app_name}-{service_name} \
                  {gunicorn_options} \
                  --user={app_name} --group={app_name} \
                  --pid /var/run/{service_path}.pid \
                  --access-logfile=/var/log/{service_path}/access.log \
                  --error-logfile=/var/log/{service_path}/error.log \
                  {service.wsgi_app}
end script

post-stop script
    rm -f /var/run/{service_path}.pid
end script

# vim: set et sw=4 ts=4 sts=4
'''.format(gunicorn_options=gunicorn_options, **format_args),
            sudo=True
        )
        instance.sudo([
            'service', instance.app.name + '-' + self.name, 'start'
        ])


class Worker(object):

    @property
    def worker_class(self):
        raise NotImplementedError('worker_class has to be provided')

    @property
    def required_apt_packages(self):
        return frozenset()

    @property
    def required_python_packages(self):
        return frozenset()


class SyncWorker(Worker):

    worker_class = 'sync'


class EventletWorker(Worker):

    worker_class = 'eventlet'

    @property
    def required_apt_packages(self):
        return frozenset(['python-eventlet'])


class GeventWorker(Worker):

    worker_class = 'gevent'

    @property
    def required_apt_packages(self):
        return frozenset(['python-gevent'])


class TornadoWorker(Worker):

    worker_class = 'tornado'

    @property
    def required_apt_packages(self):
        return frozenset(['python-tornado'])


class MeinheldWorker(Worker):

    worker_class = 'egg:meinheld#gunicorn_worker'

    @property
    def required_apt_packages(self):
        return frozenset(['build-essential', 'python-dev', 'python-greenlet',
                          'python-greenlet-dev'])

    @property
    def required_python_packages(self):
        return frozenset(['meinheld'])


#: (:class:`collections.Mapping`) The mapping of server identifier
#: strings (e.g. ``'sync'``, ``'eventlet'``) to worker classes
#: (e.g. :class:`SyncWorker`, :class:`EventletWorker`).
WORKERS = {
    'sync': SyncWorker,
    'eventlet': EventletWorker,
    'gevent': GeventWorker,
    'tornado': TornadoWorker,
    'meinheld': MeinheldWorker
}
