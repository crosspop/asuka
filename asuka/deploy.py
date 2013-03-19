""":mod:`asuka.deploy` --- Deployed branches
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
from .branch import Branch, find_by_label
from .commit import Commit

__all__ = 'Deployment',


class Deployment(object):
    """Deployment of each :attr:`branch`/:attr:`commit`."""

    #: (:class:`~asuka.app.App`) The application object.
    app = None

    #: (:class:`~asuka.branch.Branch`) The branch of the commit.
    #: It could be a pull request as well.
    branch = None

    #: (:class:`~asuka.commit.Commit`) The commit of the build.
    commit = None

    @classmethod
    def from_app(cls, app):
        """Selects the set of all deployments of the ``app``.

        :param app: the app object
        :type app: :class:`asuka.app.App`
        :returns: the set of deployments
        :rtype: :class:`collections.Set`

        """
        deployments = set()
        for instance in app.instances:
            tags = dict(instance.tags)
            try:
                branch = tags['Branch']
                commit = tags['Commit']
            except KeyError:
                continue
            live = tags.get('Live') == 'live'
            deployments.add((branch, commit, live))
        return frozenset(
            cls(branch=find_by_label(app, branch),
                commit=Commit(app, commit),
                live=live)
            for branch, commit, live in deployments
        )

    def __init__(self, branch, commit, live=False):
        if not isinstance(branch, Branch):
            raise TypeError('branch must be an instance of asuka.branch.'
                            'Branch, not ' + repr(branch))
        elif not isinstance(commit, Commit):
            raise TypeError('commit must be an instance of asuka.commit.'
                            'Commit, not ' + repr(commit))
        elif branch.app is not commit.app:
            raise TypeError('{0!r} and {1!r} are not compatible for each '
                            'other; their applications differ: {0.app!r}, '
                            'and {1.app!r}'.format(branch, commit))
        self.app = branch.app
        self.branch = branch
        self.commit = commit
        self.live = bool(live)
        instances = self.app.instances
        self.instances = instances.tagged('Branch', branch.label) \
                                  .tagged('Commit', commit.ref) \
                                  .tagged('Live', 'live' if self.live else '')

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2} {3} {4}{5}>'.format(
            c.__module__, c.__name__,
            self.app.name, self.branch.name, self.commit.ref,
            ' (live)' if self.live else ''
        )
