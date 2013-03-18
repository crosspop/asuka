""":mod:`asuka.dist` --- Package distribution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import contextlib
import datetime
import io
import optparse
import os
import os.path
import shutil
import sys
import tempfile
import threading
import time

from pip.baseparser import create_main_parser
from pip.commands import commands
from pip.exceptions import PipError
from pip.locations import src_prefix
from pip.log import Logger, logger
from pip.util import backup_dir
from pip.vcs import vcs
from pip.vcs.git import Git
from setuptools.sandbox import run_setup

from .branch import Branch
from .commit import Commit
from .logger import LoggerProviderMixin

__all__ = 'PYPI_INDEX_URLS', 'Dist', 'capture_stdout'


#: (:class:`collections.Seqeuence`) The PyPI index URLs in preferred order.
PYPI_INDEX_URLS = [
    'https://pypi.crate.io/simple/',
    'http://b.pypi.python.org/simple/',
    'http://c.pypi.python.org/simple/',
    'http://d.pypi.python.org/simple/',
    'http://e.pypi.python.org/simple/',
    'http://f.pypi.python.org/simple/',
    'http://pypi.python.org/simple/'
]


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
    def archive_package(self, cache=True):
        """Downloads the source tree and makes the source distribution.
        It yields triple of package name, filename of the source
        distribution, and its full path. ::

            with build.archive_package() as (package, filename, path):
                sftp.put(path, filename)

        :param cache: whether to cache the package file or not.
                      ``True`` by default
        :type cache: :class:`bool`

        """
        logger_ = self.get_logger('archive_package')
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
            filename = package_name + '.tar.bz2'
            if cache:
                cache_dir_path = os.path.join(
                    tempfile.gettempdir(),
                    'asuka-dist-cache'
                )
                if not os.path.isdir(cache_dir_path):
                    os.makedirs(cache_dir_path)
                cache_path = os.path.join(cache_dir_path, filename)
                if os.path.isfile(cache_path):
                    logger_.info('cache exists: %s, skipping sdist...',
                                 cache_path)
                    yield package_name, filename, cache_path
                    return
            run_setup(setup_script, [
                'egg_info', '--tag-build', tag,
                'sdist', '--formats=bztar'
            ])
            filepath = os.path.join(path, 'dist', filename)
            logger_.info('sdist_path = %r', filepath)
            if cache:
                logger_.info('save sdist cache %s...', cache_path)
                shutil.copyfile(filepath, cache_path)
            yield package_name, filename, filepath

    @contextlib.contextmanager
    def bundle_package(self, cache=True):
        """Makes the pybundle archive (that :program:`pip` can take to
        install) with completely resolved dependencies.  It yields triple
        of package name, filename of the pybundle archive, and its full
        path. ::

            with build.bundle_package() as (package, filename, path):
                sftp.put(path, filename)

        :param cache: whether to cache the package file or not.
                      ``True`` by default
        :type cache: :class:`bool`

        """
        asuka_logger = self.get_logger('bundle_package')
        # Makes pip.log.logger to forward records to the standard logging
        if not getattr(type(self), 'initialized', False):
            type(self).initialized = True
            logger.consumers.extend([
                (Logger.FATAL, asuka_logger.critical),
                (Logger.ERROR, asuka_logger.error),
                (Logger.WARN, asuka_logger.warn),
                (Logger.NOTIFY, asuka_logger.info),
                (Logger.INFO, asuka_logger.info),
                (Logger.DEBUG, asuka_logger.debug),
                (Logger.VERBOSE_DEBUG, asuka_logger.debug)
            ])
            vcs.register(Git)
        main_parser = create_main_parser()
        bundle = commands['bundle'](main_parser)
        with self.archive_package() as (package_name, filename, filepath):
            bundle_filename = package_name + '.pybundle'
            if cache:
                cache_dir_path = os.path.join(
                    tempfile.gettempdir(),
                    'asuka-pybundle-cache'
                )
                if not os.path.isdir(cache_dir_path):
                    os.makedirs(cache_dir_path)
                cache_path = os.path.join(cache_dir_path, bundle_filename)
                if os.path.isfile(cache_path):
                    asuka_logger.info('cache exists: %s, skipping pybundle...',
                                      cache_path)
                    yield package_name, bundle_filename, cache_path
                    return
            tempdir = tempfile.gettempdir()
            bundle_path = os.path.join(
                os.path.dirname(filepath),
                bundle_filename
            )
            asuka_logger.info('pybundle_path = %r', bundle_path)
            options = optparse.Values()
            options.editables = []
            options.requirements = []
            options.find_links = []
            options.index_url = PYPI_INDEX_URLS[0]
            options.extra_index_urls = PYPI_INDEX_URLS[1:]
            options.no_index = False
            options.use_mirrors = False
            options.mirrors = True
            options.build_dir = os.path.join(
                tempdir,
                'asuka-dist-build-bundle'
            )
            options.target_dir = None
            options.download_dir = None
            options.download_cache = os.path.join(
                tempdir,
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
            retrial = 0
            while 1:
                try:
                    shutil.rmtree(options.build_dir)
                except (OSError, IOError):
                    pass
                try:
                    bundle.run(options, [bundle_path, filepath])
                except PipError as e:
                    asuka_logger.exception(e)
                    retrial += 1
                    if retrial < 3:
                        asuka_logger.error(
                            'retry pip bundle after %d second(s)... (%d)',
                            retrial, retrial ** 2
                        )
                        options.index_url = PYPI_INDEX_URLS[retrial]
                        options.extra_index_urls = PYPI_INDEX_URLS[retrial+1:]
                        time.sleep(retrial ** 2)
                        continue
                    raise
                finally:
                    if os.path.isdir(options.build_dir):
                        shutil.rmtree(options.build_dir)
                break
            asuka_logger.debug('end: pip bundle %s %s', bundle_path, filepath)
            if cache:
                asuka_logger.info('save pybundle cache %s...', cache_path)
                shutil.copyfile(bundle_path, cache_path)
            yield package_name, os.path.basename(bundle_path), bundle_path


class UTC(datetime.tzinfo):
    """UTC"""

    def utcoffset(self, value):
        return datetime.timedelta(0)

    def tzname(self, value):
        return 'UTC'

    def dst(self, value):
        return datetime.timedelta(0)
