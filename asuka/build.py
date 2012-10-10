""":mod:`asuka.build` --- Each build of features
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import datetime
import io
import os
import os.path
import re
import shutil
import sys
import tempfile

from setuptools.sandbox import run_setup
from werkzeug.utils import import_string
from yaml import load

from .commit import Commit
from .instance import Instance
from .logger import LoggerProviderMixin
from .service import Service

__all__ = 'Build', 'UTC', 'capture_stdout', 'import_string'


@contextlib.contextmanager
def capture_stdout():
    """Captures the standard output (it will get silent) and yields
    the buffer.
    
    For example, the following prints nothing to console but
    ``result`` becomes ``'yeah'``::

        with capture_output() as out:
            print 'yeah'
            result = out.getvalue()

    """
    stdout = sys.stdout
    sys.stdout = io.BytesIO()
    yield sys.stdout
    sys.stdout = stdout


class Build(LoggerProviderMixin):
    """Build of commit.

    :param commit: the commit of the build
    :type commit: :class:`~asuka.commit.Commit`
    :param instance: the instance the build is/will be done.
    :type instance: :class:`~asuka.instance.Instance`

    """

    #: (:class:`re.RegexObject`) The pattern of service configuration
    #: files.
    SERVICE_FILENAME_PATTERN = re.compile(r'^(?P<name>[a-z0-9_]{2,50})\.yml$')

    #: (:class:`~asuka.app.App`) The application object.
    app = None

    #: (:class:`~asuka.commit.Commit`) The commit of the build
    commit = None

    #: (:class:`~asuka.instance.Instance`) The instance the build
    #: is/will be done.
    instance = None

    def __init__(self, commit, instance):
        if not isinstance(commit, Commit):
            raise TypeError('commit must be an instance of asuka.commit.'
                            'Commit, not ' + repr(commit))
        elif not isinstance(instance, Instance):
            raise TypeError('expected an instance of asuka.instance.'
                            'Instance, not ' + repr(instance))
        self.app = commit.app
        self.commit = commit
        self.instance = instance

    @property
    def services(self):
        """(:class:`collections.Iterable`) The list of declared
        :class:`~asuka.service.Service` objects.

        """
        filename_re = self.SERVICE_FILENAME_PATTERN
        with self.commit.download() as path:
            config_dir = os.path.join(path, self.app.config_dir)
            if not os.path.isdir(config_dir):
                return
            for name in os.listdir(config_dir):
                match = filename_re.search(name)
                if not match:
                    continue
                try:
                    with open(os.path.join(config_dir, name)) as yaml:
                        service_dict = load(yaml)
                    if not service_dict.pop('enabled', False):
                        continue
                    import_name = service_dict.pop('type')
                    service_cls = import_string(import_name)
                    if not isinstance(service_cls, type):
                        raise TypeError('type must be a class, not ' +
                                        repr(service_cls))
                    elif not issubclass(service_cls, Service):
                        raise TypeError('type must be a subtype of asuka.'
                                        'service.Service')
                    service = service_cls(
                        name=match.group('name'),
                        **service_dict
                    )
                except Exception as e:
                    raise type(e)(name + ': ' + str(e))
                yield service

    @contextlib.contextmanager
    def archive_package(self):
        """Downloads the source tree and makes the source distribution.
        It yields triple of package name, filename of the source
        distribution, and its full path. ::

            with build.archive_package() as (package, filename, path):
                sftp.put(path, filename)

        """
        with self.commit.download() as path:
            setup_script = os.path.join(path, 'setup.py')
            if not os.path.isfile(setup_script):
                raise IOError('cannot found setup.py script in the source '
                              'tree {0!r}'.format(self.commit))
            tag = '.{0}.{1:%Y%m%d%H%M%S}.{2!s:.7}'.format(
                'master', # FIXME: it should be parameterized
                self.commit.committed_at.astimezone(UTC()),
                self.commit
            )
            with capture_stdout() as buffer_:
                run_setup(setup_script, ['--fullname'])
                fullname = buffer_.getvalue().rstrip().splitlines()[-1]
            package_name = fullname + tag
            run_setup(setup_script, [
                'egg_info', '--tag-build', tag,
                'sdist', '--formats=bztar'
            ])
            filename = package_name + '.tar.bz2'
            filepath = os.path.join(path, 'dist', filename)
            yield package_name, filename, filepath

    def install(self):
        """Installs the build and required :attr:`services`
        into the :attr:`instance`.

        """
        fd, package_path = tempfile.mkstemp()
        os.close(fd)
        with self.commit.download() as download_path:
            services = list(self.services)
            config_temp_path = tempfile.mkdtemp()
            shutil.copytree(
                os.path.join(download_path, self.app.config_dir),
                os.path.join(config_temp_path, self.app.name)
            )
            with self.archive_package() as (package, filename, temp_path):
                shutil.copyfile(temp_path, package_path)
                remote_path = os.path.join('/tmp', filename)
        with self.instance:
            sudo = self.instance.sudo
            # create user for app
            sudo(['useradd', '-U', '-G', 'users,www-data', '-Mr',
                  self.app.name])
            # assume instance uses Ubuntu >= 12.04
            apt_packages = [
                'build-essential', 'python-dev', 'python-setuptools'
            ]
            sudo(['aptitude', '-q', '-y', 'install'] + apt_packages,
                 environ={'DEBIAN_FRONTEND': 'noninteractive'})
            with self.instance.sftp():
                # uploads package
                self.instance.put_file(package_path, remote_path)
                # crate.io is way faster than official PyPI mirros
                index_url = 'https://pypi.crate.io/simple/'
                sudo(['easy_install', '--index-url=' + index_url,
                      remote_path],
                     environ={'CI': '1'})
                # remove package
                self.instance.remove_file(remote_path)
                # upload config files
                self.instance.put_directory(
                    os.path.join(config_temp_path, self.app.name),
                    '/etc/' + self.app.name,
                    sudo=True
                )
            shutil.rmtree(config_temp_path)
            for service in services:
                service.install(self.instance)

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2} {3}>'.format(c.__module__, c.__name__,
                                          self.app.name, self.commit.ref)


class UTC(datetime.tzinfo):
    """UTC"""

    def utcoffset(self, value):
        return datetime.timedelta(0)

    def tzname(self, value):
        return 'UTC'

    def dst(self, value):
        return datetime.timedelta(0)
