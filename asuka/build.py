""":mod:`asuka.build` --- Each build of features
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import os
import os.path
import re
import shutil
import tempfile
import threading

from boto.route53.record import ResourceRecordSets
from pkg_resources import resource_string
from werkzeug.utils import import_string
from yaml import load

from .commit import Commit
from .dist import PYPI_INDEX_URL, Dist
from .instance import Instance
from .logger import LoggerProviderMixin

__all__ = 'Build',


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

    #: (:class:`~asuka.commit.Commit`) The commit of the build.
    commit = None

    #: (:class:`~asuka.dist.Dist`) The package distribution object.
    dist = None

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
        self.dist = Dist(commit)

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
                    service = service_cls(
                        build=self,
                        name=match.group('name'),
                        **service_dict
                    )
                except Exception as e:
                    raise type(e)(name + ': ' + str(e))
                yield service

    def install(self):
        """Installs the build and required :attr:`services`
        into the :attr:`instance`.

        """
        logger = self.get_logger('install')
        sudo = self.instance.sudo
        def setup_instance(service_manifests, service_manifests_available):
            with self.instance:
                def aptitude(*commands):
                    sudo(['aptitude', '-y'] + list(commands),
                         environ={'DEBIAN_FRONTEND': 'noninteractive'})
                # create user for app
                sudo(['useradd', '-U', '-G', 'users,www-data', '-Mr',
                      self.app.name])
                # assume instance uses Ubuntu >= 12.04
                apt_sources = re.sub(
                    r'\n#\s*(deb(?:-src)?\s+'
                    r'http://[^.]\.ec2\.archive\.ubuntu\.com/'
                    r'ubuntu/\s+[^-]+multiverse\n)',
                    lambda m: '\n' + m.group(1),
                    self.instance.read_file('/etc/apt/sources.list', sudo=True)
                )
                self.instance.write_file('/etc/apt/sources.list', apt_sources,
                                         sudo=True)
                apt_repos = set()
                apt_packages = set([
                    'build-essential', 'python-dev', 'python-setuptools',
                    'python-pip'
                ])
                with service_manifests_available:
                    while not service_manifests[0]:
                        service_manifests_available.wait()
                for service in service_manifests[1:]:
                    apt_repos.update(service.required_apt_repositories)
                    apt_packages.update(service.required_apt_packages)
                if apt_repos:
                    for repo in apt_repos:
                        sudo(['apt-add-repository', '-y', repo])
                    aptitude('update')
                with self.instance.sftp():
                    self.instance.write_file(
                        '/usr/bin/apt-fast',
                        resource_string(__name__, 'apt-fast'),
                        sudo=True
                    )
                    self.instance.write_file('/etc/apt-fast.conf', '''
_APTMGR=aptitude
DOWNLOADBEFORE=true
_MAXNUM=20
DLLIST='/tmp/apt-fast.list'
_DOWNLOADER='aria2c -c -j ${_MAXNUM} -i ${DLLIST} --connect-timeout=10 \
             --timeout=600 -m0'
DLDIR='/var/cache/apt/archives/apt-fast'
APTCACHE='/var/cache/apt/archives/'
                    ''', sudo=True)
                sudo(['chmod', '+x', '/usr/bin/apt-fast'])
                aptitude('install', 'aria2')
                sudo(['apt-fast', '-q', '-y', 'install'] + list(apt_packages),
                     environ={'DEBIAN_FRONTEND': 'noninteractive'})
        service_manifests_available = threading.Condition()
        service_manifests = [False]
        instance_setup_worker = threading.Thread(
            target=setup_instance,
            kwargs={
                'service_manifests_available': service_manifests_available,
                'service_manifests': service_manifests
            }
        )
        instance_setup_worker.start()
        fd, package_path = tempfile.mkstemp()
        os.close(fd)
        with self.commit.download() as download_path:
            service_manifests.extend(self.services)
            service_manifests[0] = True
            with service_manifests_available:
                service_manifests_available.notify()
            config_temp_path = tempfile.mkdtemp()
            shutil.copytree(
                os.path.join(download_path, self.app.config_dir),
                os.path.join(config_temp_path, self.app.name)
            )
            with self.dist.bundle_package() as (package, filename, temp_path):
                shutil.copyfile(temp_path, package_path)
                remote_path = os.path.join('/tmp', filename)
        with self.instance.sftp():
            # upload config files
            self.instance.put_directory(
                os.path.join(config_temp_path, self.app.name),
                '/etc/' + self.app.name,
                sudo=True
            )
            shutil.rmtree(config_temp_path)
            python_packages = set()
            for service in service_manifests[1:]:
                python_packages.update(service.required_python_packages)
            # uploads package
            self.instance.put_file(package_path, remote_path)
            # join instance_setup_worker
            instance_setup_worker.join()
            # crate.io is way faster than official PyPI mirros
            sudo(['pip', 'install', '-i', PYPI_INDEX_URL, remote_path] +
                 list(python_packages), environ={'CI': '1'})
            # remove package
            self.instance.remove_file(remote_path)
            for service in service_manifests[1:]:
                for cmd in service.pre_install:
                    sudo(cmd, environ={'DEBIAN_FRONTEND': 'noninteractive'})
            for service in service_manifests[1:]:
                service.install(self.instance)
            for service in service_manifests[1:]:
                for cmd in service.post_install:
                    sudo(cmd, environ={'DEBIAN_FRONTEND': 'noninteractive'})
        service_map = dict((service.name, service)
                           for service in service_manifests[1:])
        if self.app.route53_hosted_zone_id and self.app.route53_records:
            changeset = ResourceRecordSets(
                self.app.route53_connection,
                self.app.route53_hosted_zone_id,
                'Changed by Asuka: {0}, {1}'.format(self.app.name,
                                                    self.commit.ref)
            )
            from .service import DomainService
            for service_name, domain_format in self.app.route53_records.items():
                service = service_map[service_name]
                if not isinstance(service, DomainService):
                    raise TypeError(repr(service) + 'is not an instance of '
                                    'crosspop.service.DomainService')
                domain = domain_format.format(feature='branch-master') # FIXME
                service.route_domain(domain, changeset)
            if changeset.changes:
                logger.info('Route 53 changeset:\n%s', changeset.to_xml())
                changeset.commit()

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2} {3}>'.format(c.__module__, c.__name__,
                                          self.app.name, self.commit.ref)
