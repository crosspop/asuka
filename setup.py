import distutils.cmd
import os
import os.path
import re
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
    'boto == 2.6.0', 'distribute', 'github3.py == 0.5.3', 'iso8601 == 0.1.4',
    'Jinja2 == 2.6', 'paramiko == 1.7.7.2', 'Plastic == 0.1.0', 'pip == 1.3.1',
    'PyYAML == 3.10', 'requests == 1.1.0', 'waitress == 0.8.1',
    'Werkzeug >= 0.8.3'
]


dependency_links = [
    'https://github.com/dahlia/plastic/tarball/master#egg=Plastic-0.1.0'
]


def readme():
    try:
        with open(os.path.join(os.path.dirname(__file__), 'README.rst')) as f:
            readme = f.read()
    except IOError:
        pass
    pattern = re.compile(r'''
        (?P<colon> : \n{2,})?
        \s* \.\. [ ] code-block:: \s+ [^\n]+ \n
        [ \t]* \n
        (?P<block>
            (?: (?: (?: \t | [ ]{3}) [^\n]* | [ \t]* ) \n)+
        )
    ''', re.VERBOSE)
    return pattern.sub(
        lambda m: (':' + m.group('colon') if m.group('colon') else ' ::') +
                  '\n\n' +
                  '\n'.join(' ' + l for l in m.group('block').splitlines()) +
                  '\n\n',
        readme, 0
    )


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
    package_data={'asuka': ['apt-fast']},
    install_requires=install_requires,
    dependency_links=dependency_links,
    version=VERSION,
    description='A deployment system for Python web apps using GitHub and EC2',
    long_description=readme(),
    author='Hong Minhee',
    author_email='dahlia' '@' 'crosspop.in',
    maintainer='Hong Minhee',
    maintainer_email='dahlia' '@' 'crosspop.in',
    url='http://crosspop.github.com/asuka/',
    license='MIT License',
    entry_points={
        'console_scripts': [
            'asuka-server = asuka.cli:run_server'
        ]
    },
    cmdclass={'upload_doc': upload_doc}
)
