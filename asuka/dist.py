""":mod:`asuka.dist` --- Package distribution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import datetime
import io
import optparse
import os.path
import sys
import tempfile
import threading

from pip.basecommand import command_dict, load_command
from pip.locations import build_prefix, src_prefix
from pip.log import Logger, logger
from pip.util import backup_dir
from pip.vcs import vcs
from pip.vcs.git import Git
from setuptools.sandbox import run_setup

from .branch import Branch
from .commit import Commit
from .logger import LoggerProviderMixin

__all__ = 'PYPI_INDEX_URL', 'Dist', 'capture_stdout'


#: (:class:`basestring`) The preferred PyPI index URL.  (Currently Crate.io)
PYPI_INDEX_URL = 'https://pypi.crate.io/simple/'


capture_stdout_lock = threading.Lock()


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
    with capture_stdout_lock:
        stdout = sys.stdout
        sys.stdout = io.BytesIO()
        yield sys.stdout
        sys.stdout = stdout


class Dist(LoggerProviderMixin):
    """The Python package distribution."""

    #: (:class:`~asuka.app.App`) The application object.
    app = None

    #: (:class:`~asuka.branch.Branch`) The branch of the commit.
    #: It could be a pull request as well.
    branch = None

    #: (:class:`~asuka.commit.Commit`) The commit object.
    commit = None

    def __init__(self, branch, commit):
        if not isinstance(branch, Branch):
            raise TypeError('branch must be an instance of asuka.branch.'
                            'Branch, not ' + repr(branch))
        elif not isinstance(commit, Commit):
            raise TypeError('commit must be an instance of asuka.commit.'
                            'Commit, not ' + repr(commit))
        elif branch.app is not commit.app:
            raise TypeError('{0!r} and {1!r} are not compatible for each '
                            'other; their applications differ: {0.app!r} and '
                            '{1.app!r} respectively'.format(branch, commit))
        self.branch = branch
        self.commit = commit
        self.app = commit.app

    @contextlib.contextmanager
    def archive_package(self):
        """Downloads the source tree and makes the source distribution.
        It yields triple of package name, filename of the source
        distribution, and its full path. ::

            with build.archive_package() as (package, filename, path):
                sftp.put(path, filename)

        """
        with self.branch.fetch(self.commit.ref) as path:
            setup_script = os.path.join(path, 'setup.py')
            if not os.path.isfile(setup_script):
                raise IOError('cannot found setup.py script in the source '
                              'tree {0!r}'.format(self.commit))
            tag = '.{0}.{1:%Y%m%d%H%M%S}.{2!s:.7}'.format(
                self.branch.label,
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
            logger.info('sdist_path = %r', filepath)
            yield package_name, filename, filepath

    @contextlib.contextmanager
    def bundle_package(self):
        asuka_logger = self.get_logger('bundle_package')
        # Makes pip.log.logger to forward records to the standard logging
        if not getattr(type(self), 'initialized', False):
            type(self).initialized = True
            logger.consumers.extend([
                (slice(Logger.VERBOSE_DEBUG, Logger.INFO), asuka_logger.debug),
                (slice(Logger.INFO, Logger.WARN), asuka_logger.info),
                (slice(Logger.WARN, Logger.ERROR), asuka_logger.warn),
                (slice(Logger.ERROR, Logger.FATAL), asuka_logger.error),
                (slice(Logger.FATAL, None), asuka_logger.critical),
            ])
            vcs.register(Git)
            load_command('bundle')
        bundle = command_dict['bundle']
        with self.archive_package() as (package_name, filename, filepath):
            bundle_path = os.path.join(
                os.path.dirname(filepath),
                package_name + '.pybundle'
            )
            asuka_logger.info('pybundle_path = %r', bundle_path)
            options = optparse.Values()
            options.editables = []
            options.requirements = []
            options.find_links = []
            options.index_url = PYPI_INDEX_URL
            options.extra_index_urls = []
            options.no_index = False
            options.use_mirrors = False
            options.mirrors = True
            options.build_dir = backup_dir(build_prefix, '-bundle')
            options.target_dir = None
            options.download_dir = None
            options.download_cache = os.path.join(
                tempfile.gettempdir(),
                'asuka-dist-download-cache'
            )
            options.src_dir = backup_dir(src_prefix, '-bundle')
            options.upgrade = False
            options.force_reinstall = False
            options.ignore_dependencies = False
            options.no_install = True
            options.no_download = False
            options.install_options = []
            options.global_options = []
            options.use_user_site = False
            options.as_egg = False
            asuka_logger.debug('start: pip bundle %s %s', bundle_path, filepath)
            bundle.run(options, [bundle_path, filepath])
            asuka_logger.debug('end: pip bundle %s %s', bundle_path, filepath)
            yield package_name, os.path.basename(bundle_path), bundle_path


class UTC(datetime.tzinfo):
    """UTC"""

    def utcoffset(self, value):
        return datetime.timedelta(0)

    def tzname(self, value):
        return 'UTC'

    def dst(self, value):
        return datetime.timedelta(0)
