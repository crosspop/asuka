""":mod:`asuka.web` --- Web frontend
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import datetime
import functools
import hashlib
import hmac
import logging
import multiprocessing
import os
import os.path
import random
import re
import sys
import time
import traceback

from github3.api import login
from jinja2 import Environment, PackageLoader
from plastic.app import BaseApp
from plastic.rendering import render
from requests import session
try:
    import simplejson as json
except ImportError:
    import json
from werkzeug.exceptions import BadRequest, Forbidden
from werkzeug.urls import url_decode, url_encode
from werkzeug.utils import redirect

from . import urls
from .app import App
from .branch import Branch, PullRequest, find_by_label
from .build import Build, Clean, Promote
from .commit import Commit

__all__ = 'WebApp', 'auth_required', 'authorize', 'delegate', 'home', 'hook'

#: (:class:`re.RegexObject`) The pattern that makes commit ignored by Asuka.
IGNORE_PATTERN = re.compile('ASUKA\s*:\s*(SKIP|IGNORED?)*')


class WebApp(BaseApp):
    """WSGI-compliant web frontend of Asuka.

    :param app: the application object
    :type app: :class:`asuka.app.App`
    :param config: an optional config dict
    :type config: :class:`collections.Mapping`

    """

    #: (:class:`multiprocessing.Pool`) The multiprocessing pool.
    pool = None

    def __init__(self, app, config={}):
        if not isinstance(app, App):
            raise TypeError('app must be an instance of asuka.app.App, not ' +
                            repr(app))
        config.update(app.web_config)
        config['app'] = app
        super(WebApp, self).__init__(config)
        try:
            pool_size = multiprocessing.cpu_count()
        except NotImplementedError:
            pool_size = 3
        else:
            pool_size = pool_size * 2 + 1
        self.pool = multiprocessing.Pool(pool_size)

    @property
    def app(self):
        """(:class:`asuka.app.App`) The application object."""
        return self.config['app']

    def wsgi_app(self, environ, start_response):
        app = self.app
        if not app.url_base:
            url_base = '{0[wsgi.url_scheme]}://{0[HTTP_HOST]}'.format(environ)
            app.url_base = url_base
        return super(WebApp, self).wsgi_app(environ, start_response)


WebApp.associate_mimetypes({
    'text/plain': 'txt',
    'text/html': 'html'
})


#: (:class:`jinja2.Environment`) The configured environment of Jinja2
#: template engine.
jinja_env = Environment(
    loader=PackageLoader(__name__, WebApp.template_path),
    extensions=['jinja2.ext.with_']
)

# Register functions of :mod:`asuka.urls` as Jinja2 filters.
jinja_env.filters.update(
    (fn + '_url', getattr(urls, fn))
    for fn in urls.__all__
)


@WebApp.template_engine(suffix='jinja')
def render_jinja(request, path, values):
    """Renders HTML templates using Jinja2."""
    template = jinja_env.get_template(path)
    return template.render(request=request, **values)


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
        url = request.build_url('authorize', back=request.url, _external=True)
        return redirect(url)
    return decorated


@WebApp.route('/')
def authorize(request):
    """Authorizes the user using GitHub OAuth 2."""
    back = request.args.get('back', request.build_url('home', _external=True))
    if request.session.get('github_login'):
        return redirect(back)
    app = request.app.app
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
        token = response.json()['access_token']
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
    """The list of deployed branches."""
    deployments = request.app.app.deployments
    return render(request, deployments, 'home', deployments=deployments)


@WebApp.route('/deploy', methods=['POST'])
def deploy_manually(request):
    webapp = request.app
    app = webapp.app
    branch = find_by_label(app, request.form['branch'])
    commit = Commit(app, request.form['commit'])
    deploy(webapp, commit, branch)
    return 'Start to deploy {0!r} [{1.ref}]'.format(branch, commit)


@WebApp.route('/branches/<label>/terminate', methods=['POST'])
@auth_required
def terminate(request, label):
    webapp = request.app
    app = webapp.app
    branch = find_by_label(app, label)
    commit = app.deployed_branches[branch]
    cleanup(webapp, commit, branch)
    return 'Start to terminate {0!r} [{1.ref}]'.format(branch, commit)


@WebApp.route('/branches/<label>/deploy', methods=['POST'])
@auth_required
def deploy_again(request, label):
    webapp = request.app
    app = webapp.app
    branch = find_by_label(app, label)
    commit = app.deployed_branches[branch]
    redeploy(webapp, commit, branch)
    return 'Start to redeploy {0!r} [{1.ref}]'.format(branch, commit)


@WebApp.route('/branches/<label>/promote', methods=['POST'])
@auth_required
def start_promote(request, label):
    webapp = request.app
    app = webapp.app
    branch = find_by_label(app, label)
    commit = app.deployed_branches[branch]
    promote(webapp, commit, branch)
    return 'Start to promote {0!r} [{1.ref}]'.format(branch, commit)


@WebApp.route('/hook/')
def hook(request):
    logger = logging.getLogger(__name__ + '.hook')
    app = request.app.app
    assert request.mimetype == 'application/json'
    data = request.data
    sig = hmac.new(app.github_client_secret, data, hashlib.sha1)
    assert request.headers['X-Hub-Signature'].split('=')[1] == sig.hexdigest()
    payload = json.loads(data)
    event = request.headers['X-GitHub-Event']
    logger.info('event = %r', event)
    logger.debug('payload = %r', payload)
    if event == 'push':
        message = payload.get('head_commit', {}).get('message', '')
    elif event == 'pull_request':
        try:
            number = payload['pull_request']['number']
        except KeyError:
            message = None
        else:
            pull_request = app.repository.pull_request(number)
            commits = pull_request.iter_commits()
            head = list(commits)[-1]
            message = head._json_data.get('commit', {}).get('message', '')
    else:
        message = None
    if message is None or IGNORE_PATTERN.search(message):
        return 'ignored'
    config_url = app.repository._build_url('contents', app.config_dir,
                                           base_url=app.repository._api)
    config_dir = app.repository._get(config_url.rstrip('/'), params={
        'ref': payload['head_commit']['id']
               if event == 'push'
               else payload['pull_request']['head']['sha']
    })
    logger.info('config_dir.url = %r', config_dir.url)
    logger.info('config_dir.status_code = %r', config_dir.status_code)
    logger.debug('config_dir.json = %r', config_dir.json)
    if config_dir.status_code >= 400:
        return 'ignored'
    if event == 'pull_request':
        hook_pull_request(request.app, payload)
    elif event == 'push':
        hook_push(request.app, payload)
    return 'okay'


def hook_pull_request(webapp, payload):
    pull_request = payload['pull_request']
    commit = Commit(webapp.app, pull_request['head']['sha'])
    branch = PullRequest(webapp.app, pull_request['number'])
    if payload['action'] == 'closed':
        cleanup(webapp, commit, branch)
    else:
        deploy(webapp, commit, branch)


def hook_push(webapp, payload):
    commit = Commit(webapp.app, payload['after'])
    branch = Branch(webapp.app, payload['ref'].split('/', 2)[2])
    deploy(webapp, commit, branch)


def redeploy(webapp, commit, branch):
    logger = logging.getLogger(__name__ + '.redeploy')
    logger.info('start redeployment: %s [%s]', branch.label, commit.ref)
    webapp.pool.apply_async(
        redeploy_worker,
        (webapp.app, branch.label, commit.ref)
    )


def redeploy_worker(app, branch, commit):
    cleanup_worker(app, branch, commit)
    deploy_worker(app, branch, commit)


def cleanup(webapp, commit, branch):
    logger = logging.getLogger(__name__ + '.cleanup')
    logger.info('start cleaning up: %s [%s]', branch.label, commit.ref)
    webapp.pool.apply_async(
        cleanup_worker,
        (webapp.app, branch.label, commit.ref)
    )


def cleanup_worker(app, branch, commit):
    branch = find_by_label(app, branch)
    commit = Commit(app, commit)
    logger = logging.getLogger(__name__ + '.cleanup_worker')
    try:
        system_logger = logging.getLogger('asuka')
        system_logger.setLevel(logging.DEBUG)
        system_logger.addHandler(logging.StreamHandler(sys.stderr))
        logger.info('start cleanup_worker: %s [%s]', branch.label, commit.ref)
        clean = Clean(branch, commit)
        clean.uninstall()
        logger.info('finished cleanup_worker: %s [%s]',
                    branch.label, commit.ref)
    except Exception as e:
        logger.exception(e)


def promote(webapp, commit, branch):
    logger = logging.getLogger(__name__ + '.promote')
    logger.info('start promoting: %s [%s]', branch.label, commit.ref)
    webapp.pool.apply_async(
        promote_worker,
        (webapp.app, branch.label, commit.ref)
    )


def promote_worker(app, branch, commit):
    branch = find_by_label(app, branch)
    commit = Commit(app, commit)
    logger = logging.getLogger(__name__ + '.promote_worker')
    try:
        system_logger = logging.getLogger('asuka')
        system_logger.setLevel(logging.DEBUG)
        system_logger.addHandler(logging.StreamHandler(sys.stderr))
        logger.info('start cleanup_worker: %s [%s]', branch.label, commit.ref)
        # start web hook
        payload = make_payload(branch, commit)
        with session() as client:
            for hook_url in branch.app.start_hook_urls:
                client.post(
                    hook_url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(payload)
                )
        # build
        instance = branch.app.create_instance()
        promote_ = Promote(branch, commit, instance)
        deployed_domains = promote_.install()
        # finish web hook
        payload['deployed_domains'] = dict(
            (service, domain[:-1] if domain.endswith('.') else domain)
            for service, domain in deployed_domains.items()
        )
        with session() as client:
            for hook_url in branch.app.finish_hook_urls:
                client.post(
                    hook_url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(payload)
                )
        logger.info('finished promote_worker: %s [%s]',
                    branch.label, commit.ref)
    except Exception as e:
        logger.exception(e)


def deploy(webapp, commit, branch):
    logger = logging.getLogger(__name__ + '.deploy')
    logger.info('start deployment: %s [%s]', branch.label, commit.ref)
    webapp.pool.apply_async(
        deploy_worker,
        (webapp.app, branch.label, commit.ref)
    )


def make_payload(branch, commit):
    if isinstance(branch, PullRequest):
        pr = branch.pull_request
        human_label = 'pull request #{0}'.format(branch.number)
        url = pr.html_url
    else:
        pr = None
        human_label = 'branch ' + branch.name
        url = branch.app.repository.html_url + '/tree/' + branch.name
    branch = {
        'name': branch.name,
        'label': branch.label,
        'human_label': human_label,
        'url': url,
        'pull_request': {
            'number': pr and pr.number,
            'created_at': pr and pr.created_at.isoformat(),
            'links': pr and pr.links,
            'title': pr and pr.title,
            'user': {
                'name': pr and pr.user.name,
                'login': pr and pr.user.login
            }
        }
    }
    commit = {
        'ref': commit.ref,
        'short_ref': commit.ref[:8],
        'author': commit.git_commit.author,
        'committer': commit.git_commit.committer,
        'committed_at': commit.committed_at.isoformat(),
        'message': commit.git_commit.message
    }
    return {
        'branch': branch,
        'commit': commit
    }


def deploy_worker(app, branch, commit):
    branch = find_by_label(app, branch)
    commit = Commit(app, commit)
    logger = logging.getLogger(__name__ + '.deploy_worker')
    try:
        system_logger = logging.getLogger('asuka')
        system_logger.setLevel(logging.DEBUG)
        system_logger.addHandler(logging.StreamHandler(sys.stderr))
        logger.info('start deploy_worker: %s [%s]', branch.label, commit.ref)
        # start web hook
        payload = make_payload(branch, commit)
        with session() as client:
            for hook_url in branch.app.start_hook_urls:
                client.post(
                    hook_url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(payload)
                )
        # build
        instance = branch.app.create_instance()
        build = Build(branch, commit, instance)
        deployed_domains = build.install()
        # finish web hook
        payload['deployed_domains'] = dict(
            (service, domain[:-1] if domain.endswith('.') else domain)
            for service, domain in deployed_domains.items()
        )
        with session() as client:
            for hook_url in branch.app.finish_hook_urls:
                client.post(
                    hook_url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(payload)
                )
        logger.info('finished deploy_worker: %s [%s]', branch.label, commit.ref)
    except Exception as e:
        logger.exception(e)


@WebApp.route('/logs/')
@auth_required
def log_list(request):
    data_dir = request.app.app.data_dir
    entries = [
        dirname
        for dirname in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, dirname))
    ]
    entries.sort(key=lambda n: n.rsplit('.', 1)[-1], reverse=True)
    return render(request, entries, 'log_list', builds=entries)


@WebApp.route('/logs/<build>/')
@auth_required
def log_file(request, build):
    data_dir = request.app.app.data_dir
    filename = os.path.join(data_dir, build, 'log.txt')
    levelno = request.values.get('levelno', default=logging.INFO, type=int)
    thread = request.values.get('thread')
    logger = request.values.get('name')
    def records():
        with open(filename) as f:
            for number, line in enumerate(f):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    record = {
                        'parsing_error': traceback.format_exc(),
                        'line_number': number + 1
                    }
                else:
                    if logger and not record['name'].startswith(logger):
                        continue
                    if record['levelno'] < levelno:
                        continue
                    if thread and thread != (record['process_name'] +
                                             '/' + record['thread_name']):
                        continue
                    record['created_time'] = time.gmtime(record['created'])
                    record['created_string'] = time.strftime(
                        '%Y-%m-%d %H:%M:%S',
                        record['created_time']
                    )
                yield record
    return render(request, records(), 'log_file',
                  build=build, records=records(), levelno=levelno)


@WebApp.route('/delegate/')
@auth_required
def delegate(request):
    """Delegated authentication for deployed web apps."""
    back = request.args.get('back', request.referrer)
    login = request.context.github_login
    timestamp = datetime.datetime.utcnow()
    hostname = re.search(r'^https?://([^/]+)/', back).group(1)
    token = '{0:%Y%m%d%H%M%S}/{1}/{2}'.format(timestamp, login, hostname)
    secret = request.app.app.consistent_secret
    sig = hmac.new(secret, token, hashlib.sha256).hexdigest()
    back += ('&' if '?' in back else '?') + url_encode({
        'token': token,
        'sig': sig
    })
    return redirect(back)


def with_qs(url, **args):
    """Updates query string part from the ``url``.  Parameters to update
    are given by keywords.

    """
    try:
        pos = url.index('?')
    except ValueError:
        return url + '?' + url_encode(args)
    pos += 1
    query = url_decode(url[pos:], cls=dict)
    query.update(args)
    return url[:pos] + url_encode(query)


jinja_env.filters['with_qs'] = with_qs
