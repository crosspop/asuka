""":mod:`asuka.service` --- Service interface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import re

from .build import BaseBuild
from .instance import Instance

__all__ = 'DomainService', 'Service'


class Service(object):
    """The inteface of services.

    :param build: the build object
    :type build: :class:`~asuka.build.BaseBuild`
    :param name: the service name
    :type name: :class:`basestring`
    :param config: the config mapping object
    :type config: :class:`collections.Mapping`
    :param required_apt_repositories: the set of APT repositories
                                      the service uses
    :param required_apt_packages: the set of APT packages
                                  the service depends
    :type required_apt_packages: :class:`collections.Set`
    :param required_python_packages: the set of Python packages
                                     the service depends.
                                     elements have to be PyPI names
    :type required_python_packages: :class:`collections.Set`

    """

    #: (:class:`re.RegexObject`) The pattern of the valid service name.
    NAME_PATTERN = re.compile('^[a-z_][a-z0-9_]{1,50}$')

    #: (:class:`~asuka.build.Build`) The build object.
    build = None

    #: (:class:`~asuka.branch.Branch`) The branch object.
    branch = None

    #: (:class:`~asuka.commit.Commit`) The commit object.
    commit = None

    #: (:class:`~asuka.app.App`) The application object.
    app = None

    #: (:class:`str`) The service name e.g. ``'web'``.
    name = None

    #: (:class:`collections.Sequence`) The shell commands to be invoked
    #: before services are installed.
    pre_install = None

    #: (:class:`collections.Sequence`) The shell commands to be invoked
    #: after all services are installed.
    post_install = None

    #: (:class:`collections.Mapping`) The configuration dictionary.
    config = None

    def __init__(self, build, name, config={},
                 required_apt_repositories=frozenset(),
                 required_apt_packages=frozenset(),
                 required_python_packages=frozenset(),
                 pre_install=[],
                 post_install=[]):
        if not isinstance(build, BaseBuild):
            raise TypeError('build must be an instance of asuka.build.'
                            'BaseBuild, not ' + repr(build))
        elif not isinstance(name, basestring):
            raise TypeError('name must be a string, not ' + repr(name))
        elif not self.NAME_PATTERN.search(name):
            raise TypeError('invalid name: ' + repr(name))
        self.build = build
        self.app = build.app
        self.branch = build.branch
        self.commit = build.commit
        self.name = str(name)
        self.config = dict(config)
        self._required_apt_repositories = frozenset(required_apt_repositories)
        self._required_apt_packages = frozenset(required_apt_packages)
        self._required_python_packages = frozenset(required_python_packages)
        self.pre_install = list(pre_install)
        self.post_install = list(post_install)

    @property
    def required_apt_repositories(self):
        """(:class:`collections.Set`) The set of APT repository source lines
        to add.  It takes source lines :program:`apt-add-repository`
        can take e.g.::

            frozenset([
                'deb http://myserver/path/to/repo stable myrepo',
                'http://myserver/path/to/repo myrepo',
                'https://packages.medibuntu.org free non-free',
                'http://extras.ubuntu.com/ubuntu ',
                'ppa:user/repository'
            ])

        """
        return self._required_apt_repositories

    @property
    def required_apt_packages(self):
        """(:class:`collections.Set`) The set of APT package names
        to install e.g. ``frozenset(['python-dev', 'python-greenlet'])``.

        """
        return self._required_apt_packages

    @property
    def required_python_packages(self):
        """(:class:`collections.Set`) The set of PyPI_ package names
        to install e.g. ``frozenset(['Werkzeug', 'chardet'])``.

        .. _PyPI: http://pypi.python.org/

        """
        return self._required_python_packages

    def install(self, instance):
        """Installs the service into the ``instance``.

        :param instance: the instance to install the service
        :type instance: :class:`asuka.instance.Instance`

        """
        if not isinstance(instance, Instance):
            raise TypeError('instance must be an asuka.instance.Instance '
                            'object, not ' + repr(instance))
        elif instance.app is not self.app:
            raise TypeError('{0!r} is not an instance for {1!r} but {0.app!r}'
                            ''.format(instance, self.app))
        app_name = instance.app.name
        F = app_name, self.name
        with instance:
            # Make directories
            instance.do([
                'sudo', 'mkdir', '-p', '/etc/{0}/{1}'.format(*F),
                '/var/lib/{0}/{1}'.format(*F), '/var/run/{0}'.format(*F)
            ])
            instance.do([
                'sudo', 'chown', '-R', '{0}:{0}'.format(*F),
                '/etc/{0}'.format(*F), '/var/lib/{0}'.format(*F),
                '/var/run/{0}'.format(*F)
            ])

    def uninstall(self):
        """Uninstalls the service."""

    def __repr__(self):
        cls = type(self)
        return '<{0.__module__}.{0.__name__} {1!r}>'.format(cls, self.name)


class DomainService(Service):
    """The service subtype mixin which provides domain routing."""

    def route_domain(self, name, records):
        """Routes the service to the zone.

        :param name: the full domain name to map
        :type name: :class:`basestring`
        :param records: the record changeset
        :type records: :class:`boto.route53.record.ResourceRecordSets`

        """

    def remove_domain(self, name, records):
        """Removes the records for the service from the zone.

        :param name: the full domain name to deregister
        :type name: :class:`basestring`
        :param records: the record changeset
        :type records: :class:`boto.route53.record.ResourceRecordSets`

        """
