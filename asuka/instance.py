""":mod:`asuka.instance` --- Instances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import collections
import contextlib
import os
import os.path
import pipes
import socket
import threading
import time
import weakref

from boto.ec2.instance import Instance as EC2Instance
from paramiko.client import AutoAddPolicy, SSHClient
from werkzeug.datastructures import ImmutableDict

from .logger import LoggerProviderMixin

__all__ = 'REGION_AMI_MAP', 'Instance', 'Metadata'


#: (:class:`collections.Mapping`) The mapping of regions to Ubuntu
#: AMIs.
REGION_AMI_MAP = ImmutableDict({
    'ap-northeast-1': 'ami-60c77761',
    'ap-southeast-1': 'ami-a4ca8df6',
    'eu-west-1': 'ami-e1e8d395',
    'sa-east-1': 'ami-8cd80691',
    'us-east-1': 'ami-a29943cb',
    'us-west-1': 'ami-87712ac2',
    'us-west-2': 'ami-20800c10'
})

#: (:class:`collections.Mapping`) The mapping of Ubuntu AMIs to
#: user login names (for SSH).
AMI_LOGIN_MAP = ImmutableDict({
    'ami-60c77761': 'ubuntu',
    'ami-a4ca8df6': 'ubuntu',
    'ami-e1e8d395': 'ubuntu',
    'ami-8cd80691': 'ubuntu',
    'ami-a29943cb': 'ubuntu',
    'ami-87712ac2': 'ubuntu',
    'ami-20800c10': 'ubuntu'
})


class Instance(LoggerProviderMixin):
    """Thin abstraction layer upon :class:`boto.ec2.instance.Instance`
    object.  It's a combination of those three fields:

    - :attr:`app`
    - :attr:`instance`
    - :attr:`login`

    It also provides SSH/SFTP connection to the instance using
    :mod:`paramiko` module under the hood.  There are convenient
    methods like :meth:`do()`, :meth:`put_file()`, :meth:`remove_file()`
    and context managers to control sessions.

    For example, the following code connects to the instance twice::

        instance.do('echo 1')
        instance.do('echo 2')

    If you pass the instance into :keyword:`with` statement, it keeps
    the connection session until the block ends.  For example,
    the following code connects to the instance only once::

        with instance:
            instance.do('echo 1')
            instance.do('echo 2')

    Or if you use it :keyword:`with` :keyword:`as`, you can deal with
    the law-level :class:`paramiko.client.SSHClient` object::

        with instance as ssh:
            in_, out, err = ssh.exec_command('echo 1')
            print out.readline()
            print >> sys.stderr, err.readline()
            in_.close()
            out.close()
            err.close()

    It automatically check the status of the instance to make sure
    it's available to connect, and waits until it's running.
    You can explicitly wait the instance using :meth:`wait_state()`
    method::

        instance.wait_state()
        with instane:
            instance.do('echo 1')

    --- but you don't have to do like that of course.

    """

    #: (:class:`~asuka.app.App`) The app object.
    app = None

    #: (:class:`boto.ec2.instance.Instance`) The EC2 instance.
    instance = None

    #: (:class:`basestring`) The unix name to login.
    login = None

    #: (:class:`Metadata`) The tags mapping of the instance.
    metadata = None

    def __init__(self, app, instance, login):
        from .app import App
        if not isinstance(app, App):
            raise TypeError('app must be an instance of asuka.app.App, not ' +
                            repr(app))
        elif not isinstance(instance, EC2Instance):
            raise TypeError('instance must be an instance of boto.ec2.'
                            'instance.Instance, not ' + repr(instance))
        elif not isinstance(login, basestring):
            raise TypeError('login name must be a string, not ' +
                            repr(login))
        self.app = app
        self.instance = instance
        self.login = login
        self.local = threading.local()
        self.tags = Metadata(self)

    def __enter__(self):
        self.local.depth = getattr(self.local, 'depth', 0) + 1
        if self.local.depth < 2:
            self.wait_state()
            self.local.client = SSHClient()
            self.local.client.set_missing_host_key_policy(AutoAddPolicy())
            trial = 1
            logger = self.get_logger()
            while 1:
                try:
                    logger.info('try to connect %s@%s... [attempt #%d]',
                                self.login,
                                self.instance.public_dns_name,
                                trial)
                    self.local.client.connect(
                        self.instance.public_dns_name,
                        username=self.login,
                        pkey=self.app.private_key
                    )
                except socket.error as e:
                    if 60 <= e.errno <= 61 and trial <= 20:
                        time.sleep(3)
                        trial += 1
                        continue
                    logger.exception(e)
                    raise
                else:
                    break
        return self.local.client

    def __exit__(self, exc_type, exc_value, traceback):
        self.local.depth = self.local.depth - 1
        if not self.local.depth:
            self.local.client.close()
            self.get_logger().info('connection closed')
            del self.local.client

    def wait_state(self, state='running', timeout=90, tick=5):
        """Waits until the instance state becomes to the given
        goal ``state`` (default is ``'running'``).

        :param state: the goal state to wait.  default is ``'running'``
        :type state: :class:`basestring`
        :param timeout: the timeout in seconds.  default is 90 seconds
        :type timeout: :class:`numbers.Real`
        :param tick: the tick seconds to refresh.  default is 5 seconds
        :type tick: :class:`numbers.Real`

        """
        start = time.time()
        logger = self.get_logger('wait_state')
        trial = 1
        while self.instance.state != state and time.time() - start < timeout:
            logger.info('attempt #%d: watch the state of %r',
                        trial, self.instance)
            self.instance.update()
            logger.info('attempt #%d: state of %r = %r',
                        trial, self.instance, self.instance.state)
            time.sleep(tick)
            trial += 1
        if self.instance.state != state:
            raise WaitTimeoutError(number=trial, seconds=time.time() - start)

    def do(self, command, environ={}):
        """Executes the given ``command`` on the SSH connection session.
        If there's no currently running session, it implictly connects
        to the instance.

        The ``command`` can be a raw string or a sequence of commands
        to be quoted::

            instance.do('echo "Hello world"')
            instance.do(['echo', 'Hello world'])

        It can take environment variables to set::

            instance.do('date', environ={'LANG': 'ko_KR'})

        :param command: the command to execute.  if it isn't string
                        but sequence, it becomes quoted and joined
        :type command: :class:`basestring`, :class:`collections.Sequence`
        :param environ: optional environment variables
        :type environ: :class:`collections.Mapping`

        """
        if not isinstance(command, basestring):
            if isinstance(command, collections.Sequence):
                command = ' '.join(pipes.quote(c) for c in command)
            else:
                raise TypeError('command must be a string or a sequence of '
                                'strings, not ' + repr(command))
        if not isinstance(environ, collections.Mapping):
            raise TypeError('environ must be mapping, not ' +
                            repr(environ))
        for env_key, env_val in environ.items():
            command = '{0}={1} {2}'.format(env_key,
                                           pipes.quote(env_val),
                                           command)
        logger = self.get_logger('do')
        remote = self.instance.public_dns_name
        with self as client:
            in_, out, err = client.exec_command(command)
            channel = out.channel
            logger.info('[%s] %s', remote, command)
            while not channel.exit_status_ready():
                ready = False
                if channel.recv_ready():
                    out_line = out.readline()
                    if out_line:
                        logger.info('[%s: %s | stdout] %s',
                                    remote, command, out_line)
                    ready = True
                if channel.recv_stderr_ready():
                    err_line = err.readline()
                    if err_line:
                        logger.info('[%s: %s | stderr] %s',
                                    remote, command, err_line)
                    ready = True
                if not ready:
                    time.sleep(0.3)
            in_.close()
            out.close()
            err.close()
            return channel.recv_exit_status()

    def sudo(self, command, environ={}):
        """The same as :meth:`do()` except the command is executed
        by superuser.

        """
        if not isinstance(environ, collections.Mapping):
            raise TypeError('environ must be mapping, not ' +
                            repr(environ))
        envlist = [k + '=' + pipes.quote(v) for k, v in environ.items()]
        if isinstance(command, basestring):
            command = 'sudo {0} {1}'.format(' '.join(envlist), command)
        elif isinstance(command, collections.Sequence):
            command = ['sudo'] + envlist + list(command)
        else:
            raise TypeError('command must be a string or a sequence of '
                            'strings, not ' + repr(command))
        return self.do(command, environ=environ)

    @contextlib.contextmanager
    def sftp(self):
        """Opens the context manager of SFTP session.  For example::

            with instance.sftp():
                instance.put_file('local_file', 'remote_file')

        It yields the low-level :class:`paramiko.sftp_client.SFTPClient`
        as well::

            with instance.sftp() as sftp:
                print sftp.getcwd()

        """
        with self as client:
            depth = getattr(self.local, 'sftp_depth', 0)
            if not depth:
                self.local.sftp_client = client.open_sftp()
            self.local.sftp_depth = depth + 1
            yield self.local.sftp_client
            self.local.sftp_depth -= 1
            if not self.local.sftp_depth:
                self.local.sftp_client.close()

    @contextlib.contextmanager
    def open_file(self, path, mode='r'):
        """Opens the remote file as context manager::

            with instance.open_file('/tmp/test', 'w') as:
                print >> f, 'hello world'
            with instance.open_file('/tmp/test') as:
                print f.read()

        :param path: the remote path of the file to open
        :type path: :class:`basestring`
        :param mode: the opening mode e.g. ``'r'``, ``'wb'``
        :type mode: :class:`basestring`

        """
        with self.sftp() as sftp:
            fr = sftp.open(path, mode)
            fr.set_pipelined(True)
            yield fr

    def get_file(self, remote_path, local_path, sudo=False):
        """Downloads the ``remote_path`` file to the ``local_path``.

        :param remote_path: the remote path to download
        :type remote_path: :class:`basestring`
        :param local_path: the local path
        :type local_path: :class:`basestring`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`

        """
        with self:
            if sudo:
                orig_path = remote_path
                remote_path = '/tmp/' + remote_path.replace('/', '-')
                self.sudo(['cp', orig_path, remote_path])
                self.sudo(['chmod', '0777', remote_path])
            with self.sftp() as sftp:
                sftp.get(remote_path, local_path)
                if sudo:
                    self.remove_file(remote_path, sudo=True)

    def put_file(self, local_path, remote_path, sudo=False):
        """Uploads the ``local_path`` file to the ``remote_path``.

        :param local_path: the local path to upload
        :type local_path: :class:`basestring`
        :param remote_path: the remote path
        :type remote_path: :class:`basestring`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`

        """
        with self:
            if sudo:
                orig_path = remote_path
                remote_path = '/tmp/' + remote_path.replace('/', '-')
            with self.sftp() as sftp:
                sftp.put(local_path, remote_path)
            if sudo:
                self.sudo(['mv', remote_path, orig_path])
                self.sudo(['chown', 'root:root', orig_path])

    def put_directory(self, local_path, remote_path, sudo=False):
        """Uploads the ``local_path`` directory to the ``remote_path``.

        :param local_path: the local path to upload
        :type local_path: :class:`basestring`
        :param remote_path: the remote path
        :type remote_path: :class:`basestring`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`

        """
        with self.sftp():
            self.make_directory(remote_path, sudo=sudo)
            for name in os.listdir(local_path):
                if name in ('.', '..'):
                    continue
                local_name = os.path.join(local_path, name)
                remote_name = os.path.join(remote_path, name)
                if os.path.isdir(local_name):
                    f = self.put_directory
                else:
                    f = self.put_file
                f(local_name, remote_name, sudo=sudo)

    def make_directory(self, path, mode=0755, sudo=False):
        """Creates the ``path`` directory into the remote.

        :param path: the remote path of the directory to create
        :type path: :class:`basestring`
        :param mode: the permission mode of the directory
                     e.g. ``0755``
        :type mode: :class:`numbers.Integral`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`

        """
        if sudo:
            self.sudo(['mkdir', '-m{0:04o}'.format(mode), '-p', path])
        else:
            with self.sftp() as sftp:
                sftp.mkdir(path, mode)

    def read_file(self, path, sudo=False):
        """Reads the file content of the remote ``path``.
        Useful for reading configuration files.

        :param path: the remote path to read
        :type path: :class:`basestring`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`
        :return: the file content
        :rtype content: :class:`str`

        """
        with self:
            if sudo:
                orig_path = path
                path = '/tmp/' + path.replace('/', '-')
                self.sudo(['cp', orig_path, path])
                self.sudo(['chmod', '0777', path])
            with self.open_file(path, 'rb') as f:
                content = f.read()
            if sudo:
                self.remove_file(path, sudo=True)
        return content

    def write_file(self, path, content, sudo=False):
        """Writes the ``content`` to the remote ``path``.
        Useful for saving configuration files.

        :param path: the remote path to write
        :type path: :class:`basestring`
        :param content: the file content
        :type content: :class:`str`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`

        """
        if sudo:
            orig_path = path
            path = '/tmp/' + path.replace('/', '-')
        with self:
            with self.open_file(path, 'wb') as f:
                f.write(content)
            if sudo:
                self.sudo(['mv', path, orig_path])
                self.sudo(['chown', 'root:root', orig_path])

    def remove_file(self, path, sudo=False):
        """Deletes the ``path`` from the remote.

        :param path: the path to delete
        :type path: :class:`basestring`
        :param sudo: as superuser or not.  default is ``False``
        :type sudo: :class:`bool`

        """
        if sudo:
            self.sudo(['rm', path])
        else:
            with self.sftp() as sftp:
                sftp.remove(path)


class Metadata(collections.MutableMapping):
    """Metadata tags on the instances.  It can be obtained by
    :attr:`Instance.tags`.  It behaves like :class:`dict` (in other
    words, implements :class:`collections.MutableMapping`).

    :param instance: the instance that metadata belongs to
    :type instance: :class:`Instance`

    """

    def __init__(self, instance):
        if not isinstance(instance, Instance):
            raise TypeError('instance must be an asuka.instance.Instance '
                            'object, not ' + repr(instance))
        self.instance = weakref.ref(instance)

    def __len__(self):
        return len(self.instance().instance.tags)

    def __iter__(self):
        return iter(self.instance().instance.tags)

    def __getitem__(self, tag):
        if not isinstance(tag, basestring):
            raise TypeError('tag name must be a string, not ' + repr(tag))
        return self.instance().instance.tags[tag]

    def __setitem__(self, tag, value):
        if not isinstance(tag, basestring):
            raise TypeError('tag name must be a string, not ' + repr(tag))
        self.instance().instance.add_tag(tag, value)

    def __delitem__(self, tag):
        if not isinstance(tag, basestring):
            raise TypeError('tag name must be a string, not ' + repr(tag))
        self.instance().instance.remove_tag(tag)

    def update(self, mapping=[], **kwargs):
        mapping = dict(mapping, **kwargs)
        for tag in mapping:
            if not isinstance(tag, basestring):
                raise TypeError('tag name must be a string, not ' + repr(tag))
        instance = self.instance()
        instance.app.ec2_connection.create_tags(
            [instance.instance.id],
            mapping
        )
        instance.instance.tags.update(mapping)


class WaitTimeoutError(RuntimeError):
    """An error raised when the waiting hits timeout."""

    def __init__(self, number, seconds, message=None):
        if not message:
            message = 'failed to wait ({0} times, {1} seconds)'.format(
                number, seconds
            )
        super(WaitTimeoutError, self).__init__(message)
        self.number = number
        self.seconds = seconds
