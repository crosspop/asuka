""":mod:`asuka.cli` --- CLI scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import logging
import optparse
import os.path
import sys

from waitress import serve
from werkzeug.contrib.fixers import ProxyFix
from werkzeug.wsgi import DispatcherMiddleware

from .config import app_from_config_file
from .web import WebApp

__all__ = 'ForcingHTTPSMiddleware', 'run_server'


class ForcingHTTPSMiddleware(object):
    """It redirects all non-HTTPS requests to HTTPS locations."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if environ['wsgi.url_scheme'] != 'https':
            qs = environ.get('QUEYR_STRING', '')
            url = 'https://{0}{1}{2}'.format(
                environ['HTTP_HOST'],
                environ['PATH_INFO'],
                qs and '?' + qs
            )
            start_response('301 Moved Permanently', [
                ('Location', url),
                ('Content-Type', 'text/plain; charset=utf-8')
            ])
            return ['Redirecting to ', url, '...']
        return self.app(environ, start_response)


def run_server():
    """The main function of :program:`asuka-server`."""
    parser = optparse.OptionParser(usage='%prog [options] config.yml')
    parser.add_option('-H', '--host', default='0.0.0.0',
                      help='Host to listen [default: %default]')
    parser.add_option('-p', '--port', type='int', default=8080,
                      help='Port to listen [default: %default]')
    parser.add_option('--pong', help='Path which simply responds 200 OK e.g. '
                                     '--pong=/ping/')
    parser.add_option('--log-file', default='/dev/stderr',
                      help='File to write logs [default: %default]')
    parser.add_option('--proxy-fix', action='store_true', default=False,
                      help='Forward X-Forwared-* headers to support HTTP '
                           'reverse proxies e.g. nginx, lighttpd')
    parser.add_option('--force-https', action='store_true', default=False,
                      help='Redirect all HTTP requests to HTTPS locations')
    parser.add_option('-v', '--verbose', action='store_true', default=False)
    parser.add_option('-q', '--quiet', action='store_true', default=False)
    options, args = parser.parse_args()
    try:
        config, = args
    except ValueError:
        if args:
            parser.error('too many arguments')
        parser.error('missing config file')
    if not os.path.isfile(config):
        parser.error('cannot read ' + config)
    if options.verbose and options.quiet:
        parser.error('options -v/--verbose and -q/--quiet are mutually '
                     'exclusive')
    if options.log_file in ('/dev/stderr', '/dev/stdout'):
        log_file = getattr(sys, options.log_file[:-6])
    else:
        log_file = open(options.log_file, 'a')
    if options.quiet:
        logging_level = logging.ERROR
    elif options.verbose:
        logging_level = logging.DEBUG
    else:
        logging_level = logging.INFO
    logger =  logging.getLogger()
    logger.setLevel(logging_level)
    logger.addHandler(logging.StreamHandler(log_file))
    app = app_from_config_file(config)
    webapp = WebApp(app)
    if options.force_https:
        webapp.wsgi_app = ForcingHTTPSMiddleware(webapp.wsgi_app)
    if options.proxy_fix:
        webapp.wsgi_app = ProxyFix(webapp.wsgi_app)
    def pong(environ, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return ['pong']
    if options.pong:
        webapp = DispatcherMiddleware(webapp, {options.pong: pong})
    serve(webapp, host=options.host, port=options.port)
