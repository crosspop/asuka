""":mod:`asuka.cli` --- CLI scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import optparse
import os.path

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
    options, args = parser.parse_args()
    try:
        config, = args
    except ValueError:
        if args:
            parser.error('too many arguments')
        parser.error('missing config file')
    if not os.path.isfile(config):
        parser.error('cannot read ' + config)
    app = app_from_config_file(config)
    webapp = WebApp(app)
    def pong(environ, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return ['pong']
    if options.pong:
        webapp = DispatcherMiddleware(webapp, {options.pong: pong})
    serve(webapp, host=options.host, port=options.port)
