""":mod:`asuka.build` --- Each build of features
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import datetime
import json
import logging
import os
import os.path
import pprint
import re
import shutil
import tempfile
import threading
import traceback

from boto.exception import EC2ResponseError
from boto.route53.record import ResourceRecordSets
from pkg_resources import resource_string
from werkzeug.utils import import_string
from yaml import load

from .branch import Branch
from .commit import Commit
from .dist import PYPI_INDEX_URLS, Dist
from .instance import Instance
from .logger import LoggerProviderMixin

__all__ = 'BaseBuild', 'Build', 'BuildLogHandler', 'Clean', 'Promote'


class BaseBuild(LoggerProviderMixin):
    """The abstract base class of :class:`Build` and :class:`Clean`.

    :param branch: the branch of the build
    :type branch: :class:`~asuka.banch.Branch`
    :param commit: the commit of the build
    :type commit: :class:`~asuka.commit.Commit`

    """

    #: (:class:`re.RegexObject`) The pattern of service configuration
    #: files.
    SERVICE_FILENAME_PATTERN = re.compile(r'^(?P<name>[a-z0-9_]{2,50})\.yml$')

    #: (:class:`~asuka.app.App`) The application object.
    app = None

    #: (:class:`~asuka.branch.Branch`) The branch of the commit.
    #: It could be a pull request as well.
    branch = None

    #: (:class:`~asuka.commit.Commit`) The commit of the build.
    commit = None

    #: (:class:`~asuka.dist.Dist`) The package distribution object.
    dist = None

    def __init__(self, branch, commit):
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
        self.dist = Dist(branch, commit)
        self.configure_logging_handler()

    @contextlib.contextmanager
    def fetch(self):
        """The shortcut of :meth:`Branch.fetch() <asuka.branch.Branch.fetch>`
        method.  It's equivalent to::

            build.branch.fetch(build.commit.ref)

        """
        with self.branch.fetch(self.commit.ref) as path:
            yield path

    @property
    def services(self):
        """(:class:`collections.Sequence`) The list of declared
        :class:`~asuka.service.Service` objects, in topological order.

        """
        filename_re = self.SERVICE_FILENAME_PATTERN
        with self.fetch() as path:
            config_dir = os.path.join(path, self.app.config_dir)
            if not os.path.isdir(config_dir):
                return
            service_dicts = {}
            for fname in os.listdir(config_dir):
                match = filename_re.search(fname)
                if not match:
                    continue
                try:
                    with open(os.path.join(config_dir, fname)) as yaml:
                        service_dict = load(yaml)
                    if not service_dict.pop('enabled', False):
                        continue
                    service_dicts[match.group('name')] = service_dict
                except Exception as e:
                    raise type(e)(fname + ': ' + str(e))
            result = []
            visited = set()
            def visit(name, service_dict):
                if name in visited:
                    return
                visited.add(name)
                for d_name, d_service_dict in service_dicts.iteritems():
                    if name in d_service_dict.get('depends', ()):
                        visit(d_name, d_service_dict)
                try:
                    service = self.create_service(name, service_dict)
                except Exception as e:
                    raise type(e)(name + ': ' + str(e))
                result.append(service)
            for name, service_dict in service_dicts.iteritems():
                if not service_dict.get('depends'):
                    visit(name, service_dict)
            result = result[::-1]
            self.get_logger('services').info('%r', [s.name for s in result])
            return result

    def create_service(self, name, service_dict):
        """Creates an instance of :class:`~asuka.service.Service`
        from ``service_dict`` which is from an :file:`*.yml` manifest
        file.

        :param name: a service name (identifier)
        :type name: :class:`basestring`
        :param service_dict: a dictionary from an :file:`*.yml`
                             manifest file
        :type service_dit: :class:`collections.Mapping`
        :returns: created new service instance
        :rtype: :class:`asuka.service.Service`

        """
        import_name = service_dict.pop('type')
        service_cls = import_string(import_name)
        if not isinstance(service_cls, type):
            raise TypeError('type must be a class, not ' +
                            repr(service_cls))
        kwargs = dict(service_dict)
        try:
            del kwargs['depends']
        except KeyError:
            pass
        try:
            del kwargs['live_config']
        except KeyError:
            pass
        return service_cls(build=self, name=name, **kwargs)

    @property
    def route53_hosted_zone_id(self):
        """(:class:`str`) The Route 53 hosted zone ID."""
        return self.app.route53_hosted_zone_id

    @property
    def route53_records(self):
        """(:class:`collections.Mapping`) The map of service names
        and their mapped domain name format strings
        e.g. ``{'web': '{branch:label}.test.example.com.'}``.

        """
        return self.app.route53_records

    @property
    def replaced_instances(self):
        """(:class:`collections.Set`) The set of :class:`Instance
        <boto.ec2.instance.Instance>`\ s to be replaced with an instance
        built by this.

        """
        logger = self.get_logger('replaced_instances')
        try:
            reservations = self.app.ec2_connection.get_all_instances(filters={
                'tag:App': self.app.name,
                'tag:Branch': self.branch.label
            })
            instances = frozenset(
                instance
                for reservation in reservations
                for instance in reservation.instances
            )
            logger.debug('instances = %r', instances)
            return instances
        except EC2ResponseError as e:
            logger.exception(e)
            raise

    def terminate_instances(self):
        """Terminates the instances of the :attr:`branch`."""
        logger = self.get_logger('terminate_instances')
        try:
            instances = self.replaced_instances
            instance_ids = [instance.id for instance in instances]
            logger.debug('instance_ids = %r', instance_ids)
            self.app.ec2_connection.terminate_instances(instance_ids)
        except EC2ResponseError as e:
            logger.exception(e)

    @property
    def data_dir(self):
        """(:class:`basestring`) The path of directory to store data made by
        each build.

        """
        dirname = '{0.branch.label}-{0.commit!s}.{1:%Y%m%d%H%M%S}'.format(
            self, datetime.datetime.utcnow()
        )
        path = os.path.join(self.app.data_dir, dirname)
        if not os.path.isdir(path):
            os.makedirs(path)
        return path

    def configure_logging_handler(self):
        filename = os.path.join(self.data_dir, 'log.txt')
        handler = BuildLogHandler(filename, encoding='utf-8')
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger('asuka')
        logger.addHandler(handler)

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2} {3}>'.format(c.__module__, c.__name__,
                                          self.app.name, self.commit.ref)


class Build(BaseBuild):
    """Build of commit.

    :param branch: the branch of the build
    :type branch: :class:`~asuka.banch.Branch`
    :param commit: the commit of the build
    :type commit: :class:`~asuka.commit.Commit`
    :param instance: the instance the build is/will be done.
    :type instance: :class:`~asuka.instance.Instance`

    """

    #: (:class:`~asuka.instance.Instance`) The instance the build
    #: is/will be done.
    instance = None

    def __init__(self, branch, commit, instance):
        super(Build, self).__init__(branch, commit)
        if not isinstance(instance, Instance):
            raise TypeError('expected an instance of asuka.instance.'
                            'Instance, not ' + repr(instance))
        elif not (branch.app is commit.app is instance.app):
            raise TypeError('{0!r}, {1!r} and {2!r} are not compatible for '
                            'each other; their applications differ: '
                            '{0.app!r}, {1.app!r}, and {2.app!r} '
                            'respectively'.format(branch, commit, instance))
        self.instance = instance

    def install(self):
        """Installs the build and required :attr:`services`
        into the :attr:`instance`.

        :returns: the map of deployed services to these domain names
                  e.g. ``{'elb': 'pull-123.test.example.com.'}``
        :rtype: :class:`collections.Mapping`

        """
        logger = self.get_logger('install')
        sudo = self.instance.sudo
        logger.info(
            'START TO INSTALL: branch = %r, commit = %r, instance = %r',
            self.branch, self.commit, self.instance
        )
        def setup_instance(service_manifests, service_manifests_available):
            logger = self.get_logger('install.setup_instance')
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
        # setup metadata of the instance
        self.update_instance_metadata({'Status': 'started'})
        # making package (pybundle)
        fd, package_path = tempfile.mkstemp()
        os.close(fd)
        with self.fetch() as download_path:
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
                self.instance.tags['Status'] = 'apt-installed'
                pip_cmd = (
                    ['pip', 'install', '-i', PYPI_INDEX_URLS[0]] +
                    ['--extra-index-url=' + idx for idx in PYPI_INDEX_URLS[1:]]
                )
                sudo(pip_cmd + [remote_path], environ={'CI': '1'})
                sudo(pip_cmd + ['-I'] + list(python_packages),
                     environ={'CI': '1'})
                self.instance.tags['Status'] = 'installed'
                for service in service_manifests[1:]:
                    for cmd in service.pre_install:
                        sudo(cmd, environ={'DEBIAN_FRONTEND': 'noninteractive'})
                values_path = '/etc/{0}/values.json'.format(self.app.name)
                service_values = {
                    '.build': dict(
                        commit=self.commit.ref,
                        branch=self.branch.label
                    )
                }
                refresh_values = lambda: self.instance.write_file(
                    values_path,
                    json.dumps(service_values),
                    sudo=True
                )
                refresh_values()
                for service in service_manifests[1:]:
                    service_value = service.install(self.instance)
                    service_values[service.name] = service_value
                    refresh_values()
                for service in service_manifests[1:]:
                    for cmd in service.post_install:
                        sudo(cmd, environ={'DEBIAN_FRONTEND': 'noninteractive'})
        service_map = dict((service.name, service)
                           for service in service_manifests[1:])
        deployed_domains = {}
        if self.route53_hosted_zone_id and self.route53_records:
            self.instance.tags['Status'] = 'run'
            changeset = ResourceRecordSets(
                self.app.route53_connection,
                self.route53_hosted_zone_id,
                'Changed by Asuka: {0}, {1} [{2}]'.format(self.app.name,
                                                          self.branch.label,
                                                          self.commit.ref)
            )
            from .service import DomainService
            for service_name, domain_format in self.route53_records.items():
                service = service_map[service_name]
                if not isinstance(service, DomainService):
                    raise TypeError(repr(service) + 'is not an instance of '
                                    'crosspop.service.DomainService')
                domain = domain_format.format(branch=self.branch)
                deployed_domains[service_name] = domain
                service.route_domain(domain, changeset)
            if changeset.changes:
                logger.info('Route 53 changeset:\n%s', changeset.to_xml())
                changeset.commit()
        self.instance.tags['Status'] = 'done'
        self.terminate_instances()
        return deployed_domains

    @property
    def instance_name(self):
        """(:class:`basestring`) The name of the installed :attr:`instance`."""
        return '{0}-{1}-{2}'.format(
            self.app.name,
            self.branch.label,
            self.commit.ref[:8]
        )

    @property
    def live(self):
        """(:class:`basestring`) ``'live'`` (which is evaluated as ``True``
        in boolean context) if it's live or an empty string (``''`` which
        is evaluated as ``False`` in boolean context).

        """
        return ''

    def update_instance_metadata(self, tags={}):
        """Update the metadata of the installed :attr:`instance`.
        It optionally takes extra tags to set.

        :param tags: the tags mapping to set to the :attr:`instance`
        :type tags: :class:`collections.Mapping`

        """
        tag_dict = dict(
            Name=self.instance_name,
            App=self.app.name,
            Branch=self.branch.label,
            Commit=self.commit.ref,
            Live=self.live
        )
        tag_dict.update(tags)
        self.instance.tags.update(**tag_dict)

    @property
    def replaced_instances(self):
        instances = super(Build, self).replaced_instances
        return frozenset(
            instance
            for instance in instances
            if instance.tags.get('Commit', '').strip() != self.commit.ref and
               instance.tags.get('Live', '') == self.live
        )


class Clean(BaseBuild):

    def uninstall(self):
        """Uninstalls the :attr:`services`, cleans up the domains, and
        terminate instances.

        """
        logger = self.get_logger('uninstall')
        self.terminate_instances()
        service_map = dict((s.name, s) for s in self.services)
        if self.route53_hosted_zone_id and self.route53_records:
            changeset = ResourceRecordSets(
                self.app.route53_connection,
                self.route53_hosted_zone_id,
                'Changed by Asuka: {0}, {1} [clean]'.format(self.app.name,
                                                            self.branch.label)
            )
            from .service import DomainService
            for service_name, domain_format in self.route53_records.items():
                service = service_map[service_name]
                if not isinstance(service, DomainService):
                    raise TypeError(repr(service) + 'is not an instance of '
                                    'crosspop.service.DomainService')
                domain = domain_format.format(branch=self.branch)
                service.remove_domain(domain, changeset)
            if changeset.changes:
                logger.info('Route 53 changeset:\n%s', changeset.to_xml())
                changeset.commit()
        for name, service in service_map.iteritems():
            logger.info('Uninstall %s...', name)
            service.uninstall()
            logger.info('Uninstalled %s', name)


class Promote(Build):
    """Promote the master branch as live."""

    @property
    def route53_hosted_zone_id(self):
        return self.app.route53_live_hosted_zone_id

    @property
    def route53_records(self):
        return self.app.route53_live_records

    def create_service(self, name, service_dict):
        service_dict = dict(service_dict)
        config = service_dict.setdefault('config', {})
        config.update(service_dict.pop('live_config', {}))
        return super(Promote, self).create_service(name, service_dict)

    @property
    def instance_name(self):
        return '{0}-live-{1}'.format(self.app.name, self.commit.ref[:8])

    @property
    def live(self):
        return 'live'


class BuildLogHandler(logging.FileHandler):
    """Specialized logging handler for build process.  It serializes
    each :class:`~logging.LogRecord` to JSON objects.

    """

    def emit(self, record):
        try:
            stream = self.stream
            json.dump({
                'name': record.name,
                'created': record.created,
                'levelname': record.levelname,
                'levelno': record.levelno,
                'pathname': record.pathname,
                'lineno': record.lineno,
                'module': record.module,
                'func_name': record.funcName,
                'thread_name': record.threadName,
                'process_name': record.processName,
                'msg': record.msg,
                'args': pprint.pformat(record.args),
                'message': self.format(record),
                'traceback': record.exc_info and traceback.format_exception(
                    *record.exc_info
                )
            }, stream)
            stream.write('\n')
            self.flush()
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)
