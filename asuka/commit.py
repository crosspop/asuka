""":mod:`asuka.commit` --- Git commits
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import re

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

    def __str__(self):
        return self.ref

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2}:{3}>'.format(c.__module__, c.__name__,
                                          self.app.name, self.ref)
