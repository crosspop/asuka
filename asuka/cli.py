""":mod:`asuka.cli` --- CLI scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import logging
import optparse
import os.path
import sys

from waitress import serve
from werkzeug.wsgi import DispatcherMiddleware

from .config import app_from_config_file
from .web import WebApp

__all__ = 'run_server',


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
    def pong(environ, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return ['pong']
    if options.pong:
        webapp = DispatcherMiddleware(webapp, {options.pong: pong})
    serve(webapp, host=options.host, port=options.port)
