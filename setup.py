import distutils.cmd
import os
import os.path
import shutil
import tempfile

try:
    from setuptools import setup, find_packages
except ImportError:
    from distribute_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages

from asuka.version import VERSION


install_requires = [
    'boto == 2.6.0', 'distribute', 'github3.py == 0.1a8', 'iso8601 == 0.1.4',
    'paramiko == 1.7.7.2', 'PyYAML == 3.10', 'Werkzeug == 0.8.3'
]


def readme():
    try:
        with open('README.rst') as f:
            return f.read()
    except IOError:
        pass


class upload_doc(distutils.cmd.Command):
    """Uploads the documentation to GitHub pages."""

    description = __doc__
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        path = tempfile.mkdtemp()
        build = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'build', 'sphinx', 'html')
        os.chdir(path)
        os.system('git clone git@github.com:crosspop/asuka.git .')
        os.system('git checkout gh-pages')
        os.system('git rm -r .')
        os.system('touch .nojekyll')
        os.system('cp -r ' + build + '/* .')
        os.system('git stage .')
        os.system('git commit -a -m "Documentation updated."')
        os.system('git push origin gh-pages')
        shutil.rmtree(path)


setup(
    name='Asuka',
    packages=find_packages(),
    install_requires=install_requires,
    version=VERSION,
    description='A deployment system for Python web apps using GitHub and EC2',
    long_description=readme(),
    author='Hong Minhee',
    author_email='dahlia' '@' 'crosspop.in',
    maintainer='Hong Minhee',
    maintainer_email='dahlia' '@' 'crosspop.in',
    url='https://github.com/crosspop/asuka',
    license='MIT License'
)
