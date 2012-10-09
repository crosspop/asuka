""":mod:`asuka.instance` --- Instances
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import socket
import time

from boto.ec2.instance import Instance as EC2Instance
from paramiko.client import AutoAddPolicy, SSHClient

from .app import App
from .logger import LoggerProviderMixin

__all__ = 'Instance',


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

    --- but you don't have to of course.

    """

    #: (:class:`~asuka.app.App`) The app object.
    app = None

    #: (:class:`boto.ec2.instance.Instance`) The EC2 instance.
    instance = None

    #: (:class:`basestring`) The unix name to login.
    login = None

    def __init__(self, app, instance, login):
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
        self.depth = 0
        self.sftp_depth = 0

    def __enter__(self):
        self.depth += 1
        if self.depth < 2:
            self.wait_state()
            self.client = SSHClient()
            self.client.set_missing_host_key_policy(AutoAddPolicy())
            trial = 1
            logger = self.get_logger()
            while 1:
                try:
                    logger.info('try to connect %s@%s... [attempt #%d]',
                                self.login,
                                self.instance.public_dns_name,
                                trial)
                    self.client.connect(self.instance.public_dns_name,
                                        username=self.login,
                                        pkey=self.app.private_key)
                except socket.error as e:
                    if 60 <= e.errno <= 61 and trial <= 20:
                        time.sleep(3)
                        trial += 1
                        continue
                    logger.exception(e)
                    raise
                else:
                    break
        return self.client

    def __exit__(self, exc_type, exc_value, traceback):
        self.depth -= 1
        if not self.depth:
            self.client.close()
            self.get_logger().info('connection closed')
            del self.client

    def wait_state(self, state='running', timeout=60, tick=5):
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

    def do(self, command):
        logger = self.get_logger('do')
        remote = self.instance.public_dns_name
        with self as client:
            in_, out, err = client.exec_command(command)
            logger.info('[%s] %s', remote, command)
            while 1:
                out_line = out.readline()
                err_line = err.readline()
                if not (out_line or err_line):
                    break
                if out_line:
                    logger.info('[%s: %s | stdout] %s',
                                remote, command, out_line)
                if err_line:
                    logger.info('[%s: %s | stderr] %s',
                                remote, command, err_line)
            in_.close()
            out.close()
            err.close()

    @contextlib.contextmanager
    def sftp(self):
        with self as client:
            if not self.sftp_depth:
                self.sftp_client = client.open_sftp()
            self.sftp_depth += 1
            yield self.sftp_client
            self.sftp_depth -= 1
            if not self.sftp_depth:
                self.sftp_client.close()

    @contextlib.contextmanager
    def open_file(self, path, mode='r'):
        with self.sftp() as sftp:
            fr = sftp.open(path, mode)
            fr.set_pipelined(True)
            yield fr

    def put_file(self, local_path, remote_path):
        with self.sftp() as sftp:
            sftp.put(local_path, remote_path)

    def remove_file(self, path):
        with self.sftp() as sftp:
            sftp.remove(path)


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
