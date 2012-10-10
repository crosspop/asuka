""":mod:`asuka.service` --- Service interface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import re

from .instance import Instance

__all__ = 'Service',


class Service(object):
    """The inteface of services.

    :param name: the service name
    :type name: :class:`basestring`
    :param config: the config mapping object
    :type config: :class:`collections.Mapping`
    :param required_apt_packages: the set of APT packages
                                  the service depends
    :type required_apt_packages: :class:`collections.Set`
    :param required_python_packages: the set of Python packages
                                     the service depends.
                                     elements have to be PyPI names
    :type required_python_packages: :class:`collections.Set`

    """

    #: (:class:`re.RegexObject`) The pattern of the valid service name.
    NAME_PATTERN = re.compile('^[a-z0-9_]{2,50}$')

    #: (:class:`str`) The service name e.g. ``'web'``.
    name = None

    #: (:class:`collections.Mapping`) The configuration dictionary.
    config = None

    def __init__(self, name, config={},
                 required_apt_packages=frozenset(),
                 required_python_packages=frozenset()):
        if not isinstance(name, basestring):
            raise TypeError('name must be a string, not ' + repr(name))
        elif not self.NAME_PATTERN.search(name):
            raise TypeError('invalid name: ' + repr(name))
        self.name = str(name)
        self.config = dict(config)
        self._required_apt_packages = frozenset(required_apt_packages)
        self._required_python_packages = frozenset(required_python_packages)

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
        apt_packages = list(self.required_apt_packages)
        python_packages = list(self.required_python_packages)
        app_name = instance.app.name
        F = app_name, self.name
        with instance:
            # Install required_apt_packages
            instance.sudo(['aptitude', '-q', '-y', 'install'] + apt_packages,
                          environ={'DEBIAN_FRONTEND': 'noninteractive'})
            # Install required_python_packages
            index_url = 'https://pypi.crate.io/simple/'
            instance.sudo(['easy_install', '--index-url=' + index_url] +
                           python_packages,
                          environ={'CI': '1'})
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
