""":mod:`asuka.commit` --- Git commits
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import os
import os.path
import re
import shutil
import tarfile
import tempfile

from iso8601 import parse_date
from werkzeug.utils import cached_property

from .app import App
from .logger import LoggerProviderMixin

__all__ = 'Commit',


class Commit(LoggerProviderMixin):
    """The single commit.

    :param app: the application object
    :type app: :class:`~asuka.app.App`
    :param ref: the commit SHA1 hexadecimal ref id
    :type ref: :class:`basestring`

    """

    #: (:class:`re.RegexObject`) The regular expression pattern to match
    #: valid ref ids.
    REF_PATTERN = re.compile(r'^[A-Fa-f0-9]{6,40}$')

    #: (:class:`asuka.app.App`) The application object.
    app = None

    #: (:class:`basestring`) The 40 characters string of the reference id.
    #: It guarantees always full ref id.
    ref = None

    def __init__(self, app, ref):
        if not isinstance(app, App):
            raise TypeError('app must be an instance of asuka.app.App, not ' +
                            repr(app))
        elif not isinstance(ref, basestring):
            raise TypeError('ref must be a string, not ' + repr(ref))
        elif not self.REF_PATTERN.match(ref):
            raise ValueError('{0!r} is not valid ref id'.format(ref))
        self.app = app
        self.ref = str(ref)
        if len(self.ref) < 40:
            self.ref = self.repo_commit.sha
        self.download_depth = 0

    @cached_property
    def repo_commit(self):
        """(:class:`github3.repos.RepoCommit`) The signle repo commit."""
        return self.app.repository.commit(self.ref)

    @cached_property
    def git_commit(self):
        """(:class:`github3.git.Commit`) The signle git commit."""
        return self.app.repository.git_commit(self.ref)

    @cached_property
    def committed_at(self):
        """(:class:`datetime.datetime`) The tz-aware time the commit
        was made.

        """
        return parse_date(self.git_commit.committer['date'])

    @contextlib.contextmanager
    def download(self):
        """Downloads the source tree and yields the path of the tree
        managed by context.

        Usage::

            import os.path

            with ref.download() as tree_path:
                with open(os.path.join(tree_path, 'setup.py')) as setup:
                    setup_script = setup.read()

        """
        self.download_depth += 1
        if self.download_depth > 1:
            yield self.download_directory
        else:
            logger = self.get_logger('download')
            fd, tempname = tempfile.mkstemp()
            os.close(fd)
            logger.debug('start download: %s', tempname)
            self.app.repository.archive('tarball', tempname, self.ref)
            logger.debug('finish download: %s', tempname)
            path = tempfile.mkdtemp()
            logger.debug('start extract: %s', path)
            tar = tarfile.open(tempname)
            tar.extractall(path)
            root_name = tar.getnames()[0]
            tar.close()
            logger.debug('finish extract: %s', path)
            os.unlink(tempname)
            working_dir = os.path.join(path, root_name)
            self.download_directory = working_dir
            logger.debug('root path: %s', working_dir)
            yield working_dir
            shutil.rmtree(path)
            logger.debug('directory removed: %s', path)
        self.download_depth -= 1

    def __str__(self):
        return self.ref

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2}:{3}>'.format(c.__module__, c.__name__,
                                          self.app.name, self.ref)
