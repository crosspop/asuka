""":mod:`asuka.web` --- Web frontend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import functools
import random

from github3.api import login
from plastic.app import BaseApp
from requests import session
from werkzeug.exceptions import BadRequest, Forbidden
from werkzeug.urls import url_encode
from werkzeug.utils import redirect

from .app import App

__all__ = 'WebApp', 'auth_required', 'authorize', 'home'


class WebApp(BaseApp):
    """WSGI-compliant web frontend of Asuka.

    :param app: the application object
    :type app: :class:`asuka.app.App`
    :param config: an optional config dict
    :type config: :class:`collections.Mapping`

    """

    def __init__(self, app, config={}):
        if not isinstance(app, App):
            raise TypeError('app must be an instance of asuka.app.App, not ' +
                            repr(app))
        config['app'] = app
        super(WebApp, self).__init__(config)


def auth_required(function):
    """The decorator which makes the given view ``function`` to require
    authorization.
    ::

        @WebApp.route('/path/')
        @auth_required
        def view_func(request):
            return 'authorized: ' + request.context.github_login

    """
    @functools.wraps(function)
    def decorated(request, *args, **kwargs):
        github_login = request.session.get('github_login')
        if github_login:
            request.context.github_login = github_login
            return function(request, *args, **kwargs)
        return redirect(request.build_url('authorize'))
    return decorated


@WebApp.route('/')
def authorize(request):
    """Authorizes the user using GitHub OAuth 2."""
    back = request.args.get('back', request.build_url('home'))
    if request.session.get('github_login'):
        return redirect(back)
    app = request.app.config['app']
    code = request.args.get('code')
    if not code:
        # Step 1
        state = '{0:040x}'.format(random.randrange(256 ** 20))
        request.session.update(state=state, back=back)
        params = url_encode({
            'client_id': app.github_client_id,
            'redirect_uri': request.build_url('authorize', _external=True),
            'scope': 'user,repo,repo:status',
            'state': state
        })
        return redirect('https://github.com/login/oauth/authorize?' + params)
    # Step 2
    if request.args['state'] != request.session['state']:
        raise BadRequest()
    with session() as client:
        response = client.get(
            'https://github.com/login/oauth/access_token',
            params={
                'client_id': app.github_client_id,
                'client_secret': app.github_client_secret,
                'redirect_uri': request.build_url('authorize', _external=True),
                'code': code,
                'state': request.session['state']
            },
            headers={'Accept': 'application/json'}
        )
        token = response.json['access_token']
        gh = login(token=token)
        if not app.repository.is_collaborator(gh.user().login):
            raise Forbidden()
        request.session['github_login'] = gh.user().login
        del request.session['state']
        back = request.session.pop('back')
        return redirect(back)


@WebApp.route('/home/')
@auth_required
def home(request):
    """The home page."""
    return 'Hi, ' + request.context.github_login
