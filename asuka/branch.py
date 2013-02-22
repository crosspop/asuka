""":mod:`asuka.branch` --- Branches
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import numbers
import os
import os.path
import re
import sys
import tempfile

from werkzeug.utils import cached_property

from .app import App
from .logger import LoggerProviderMixin

__all__ = 'Branch', 'GitMergeError', 'PullRequest', 'find_by_label'


def find_by_label(app, label):
    """Finds the branch by its ``label`` string.

    :param app: the application object
    :type app: :class:`~asuka.app.App`
    :param label: the label got from :attr:`Branch.label` property
    :type label: :class:`str`

    """
    m = re.match(r'^branch-(.*)$', str(label))
    if m:
        return Branch(app, m.group(1))
    m = re.match(r'^pull-([1-9]\d*)$', label)
    if m:
        return PullRequest(app, int(m.group(1)), merge_test=False)
    raise ValueError('invalid label: ' + repr(label))


class Branch(LoggerProviderMixin):
    """The branch line for continuous deployment.

    :param app: the application object
    :type app: :class:`~asuka.app.App`
    :param name: the branch name e.g. ``'master'``
    :type name: :class:`basestring`

    """

    #: (:class:`~asuka.app.App`) The application object.
    app = None

    #: (:class:`basestring`) The branch name.
    name = None

    def __init__(self, app, name):
        if not isinstance(app, App):
            raise TypeError('app must be an instance of asuka.app.App, not '
                            + repr(app))
        elif not isinstance(name, basestring):
            raise TypeError('name must be a string, not ' + repr(name))
        self.app = app
        self.name = name
        self.fetched_paths = {}

    @property
    def label(self):
        """(:class:`str`) The label string readable by both human
        and machine.  It's **identifiable from other branches**, so it
        can be used as subdomain name or path name.

        """
        name = self.name
        if isinstance(name, unicode):
            name = name.encode('utf-8')
        return 'branch-{0}'.format(name.replace('_', '-'))

    @property
    def label_(self):
        """(:class:`str`) The almost same to :attr:`label` except it uses
        underscores instead of hyphens for label.

        """
        return self.label.replace('-', '_')

    @property
    def repository(self):
        """(:class:`github3.repos.Repository`) The repository the branch
        belongs to.  If it's from pull request, the branch may not be
        the same to the :attr:`app`'s main :attr:`~asuka.app.App.repository`.

        """
        return self.app.repository

    @contextlib.contextmanager
    def fetch(self, ref):
        """Downloads the source three and yields the path of the tree
        managed by context. ::

            import os.path

            with branch.fetch() as tree_path:
                with open(os.path.join(tree_path, 'setup.py')) as setup:
                    setup_script = setup.read()

        """
        if ref in self.fetched_paths:
            yield self.fetched_paths[ref]
            return
        logger = self.get_logger('download')
        app = self.app
        app_repo = app.repository
        path = os.path.join(
            '/tmp' if sys.platform == 'darwin' else tempfile.mkdtemp(),
            'asuka-{0}-{1}'.format(app.name, app_repo.id)
        )
        master = app_repo.master_branch or app_repo._json_data['default_branch']
        def run(command, *args, **kwargs):
            cmd = command.format(*args, **kwargs)
            logger.info('%s', cmd)
            with os.popen(cmd) as f:
                for line in f:
                    logger.debug('[%s] %s', cmd, line)
        def git(command, *args, **kwargs):
            command = ('git --git-dir="{git_dir}" '
                       '--work-tree="{work_tree}" ' + command)
            kwargs.update(git_dir=os.path.join(path, '.git'),
                          work_tree=path)
            run(command, *args, **kwargs)
        remote = app.get_clone_url()
        if os.path.isdir(path):
            git('reset --hard')
            git('checkout "{0}"', master)
            git('pull "{0}" "{1}"', remote, master)
        else:
            run('git clone "{0}" "{1}"', remote, path)
        if self.name != master:
            git('checkout "{0}"', self.name)
        git('pull "{0}" "{1}":"{1}"', remote, self.name),
        git('checkout "{0}"', ref)
        logger.debug('root path: %s', path)
        self.fetched_paths[ref] = path
        yield path
        del self.fetched_paths[ref]
        git('checkout "{0}"', master)
        if self.name != master:
            git('branch -D "{0}"', self.name)
        git('reset --hard')

    def __eq__(self, operand):
        if isinstance(operand, type(self)):
            return self.label == operand.label
        return False

    def __ne__(self, operand):
        return not (self == operand)

    def __hash__(self):
        return hash(self.name)

    @property
    def url(self):
        master_branch = (self.repository.master_branch or
                         self.repository._json_data['default_branch'])
        if master_branch == self.name:
            return '{0}/tree/{1}'.format(self.repository.html_url, self.name)
        return '{0}/compare/{1}...{2}'.format(
            self.repository.html_url,
            master_branch,
            self.name
        )

    def __unicode__(self):
        return 'Branch ' + self.name

    def __html__(self):
        return '<a href="{0}">{1}</a>'.format(self.url, unicode(self))

    def __repr__(self):
        c = type(self)
        fmt = '<{0}.{1} {2.app.name}:{2.name}>'
        return fmt.format(c.__module__, c.__name__, self)


class PullRequest(Branch):
    """The ad-hoc branch made by pull requests.

    :param app: the application object
    :type app: :class:`~asuka.app.App`
    :param number: the pull request number
    :type number: :class:`numbers.Integral`
    :param merge_test: raise :exc:`GitMergeError` if the pull request
                       cannot be merged.  if it's ``False`` don't
                       test mergeability of the pull request.
                       ``True`` by default
    :type merge_test: :class:`bool`
    :raises GitMergeError: if ``merge_test`` is ``True`` (default) and
                           the pull request cannot be merged into the
                           master branch

    """

    #: (:class:`github3.pulls.PullRequest`) The GitHub pull request object.
    pull_request = None

    def __init__(self, app, number, merge_test=True):
        if not isinstance(number, numbers.Integral):
            raise TypeError('number must be an integer, not ' + repr(number))
        pr = app.repository.pull_request(number)
        if not pr:
            raise ValueError("pull request #{0} can't be found".format(number))
        if merge_test:
            for x in xrange(10):
                mergeable = pr.mergeable
                if mergeable is None or x < 2:
                    pr = app.repository.pull_request(number)
                    continue
                break
            if not mergeable:
                msg = '{0!r} cannot be merged [{1!r}]'.format(pr, mergeable)
                raise GitMergeError(msg)
        super(PullRequest, self).__init__(app, pr.base.ref)
        self.pull_request = pr
        self.number = number

    @property
    def label(self):
        return 'pull-{0}'.format(self.number)

    @cached_property
    def repository(self):
        return self.app.github.repository(*self.pull_request.head.repo)

    @contextlib.contextmanager
    def fetch(self, ref):
        if ref in self.fetched_paths:
            yield self.fetched_paths[ref]
            return
        logger = self.get_logger('fetch')
        with super(PullRequest, self).fetch(self.name) as path:
            def git(command, *args, **kwargs):
                command = ('git --git-dir="{git_dir}" '
                           '--work-tree="{work_tree}" ' + command)
                kwargs.update(git_dir=os.path.join(path, '.git'),
                              work_tree=path)
                cmd = command.format(*args, **kwargs)
                logger.info('%s', cmd)
                with os.popen(cmd) as f:
                    for line in f:
                        logger.debug('[%s] %s', cmd, line)
            git('checkout {0}', self.pull_request.base.sha)
            git('checkout -b asuka-pullreq-{0}', self.number)
            git('pull "{0}" "{1}":asuka-pullreq-{2}',
                self.app.get_clone_url(self.repository),
                self.pull_request.head.ref,
                self.number)
            git('checkout "{0}"', self.name)
            git('checkout -b asuka-mergedpullreq-{0}', self.number)
            git('merge "{0}"', ref)
            git('branch -D asuka-pullreq-{0}', self.number)
            yield path
            git('checkout "{0}"', self.name)
            git('branch -D asuka-mergedpullreq-{0}', self.number)

    @property
    def url(self):
        return self.pull_request.html_url

    def __unicode__(self):
        return 'Pull Request #{0}'.format(self.number)

    def __hash__(self):
        return self.number

    def __repr__(self):
        c = type(self)
        fmt = '<{0}.{1} {2.app.name} #{2.number}>'
        return fmt.format(c.__module__, c.__name__, self)


class GitMergeError(EnvironmentError):
    """The error which rise when two Git branches cannot be merged."""
