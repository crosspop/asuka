try:
    from setuptools import setup, find_packages
except ImportError:
    from distribute_setup import use_setuptools
    use_setuptools()
    from setuptools import setup, find_packages

from asuka.version import VERSION


install_requires = [
    'boto == 2.6.0', 'github3.py == 0.1a8', 'paramiko == 1.7.7.2',
    'PyYAML == 3.10'
]


def readme():
    try:
        with open('README.rst') as f:
            return f.read()
    except IOError:
        pass


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
