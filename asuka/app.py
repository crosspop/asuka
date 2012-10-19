""":mod:`asuka.app` --- Application configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import io

from boto.ec2.connection import EC2Connection
from boto.route53.connection import Route53Connection
from github3.github import GitHub
from github3.repos import Repository
from paramiko.pkey import PKey
from paramiko.rsakey import RSAKey
from werkzeug.utils import cached_property

__all__ = 'App',


class App(object):
    """Application configuration.  It takes keyword-only parameters
    that are the same name of settable properties and attributes.

    """

    #: (:class:`basestring`) The format string of AWS key pair name.
    KEY_PAIR_NAME_FORMAT = 'Asuka-{app.name}'

    #: (:class:`basestring`) The name of the app.
    name = None

    #: (:class:`boto.ec2.connection.EC2Connection`) The EC2 connection
    #: to invoke APIs.
    ec2_connection = None

    #: (:class:`basestring`) The name of the Asuka configuration directory
    #: in the repository.  The default uses :file:`asuka/`.
    config_dir = 'asuka/'

    #: (:class:`str`) The Route 53 hosted zone ID.
    route53_hosted_zone_id = None

    #: (:class:`collections.Mapping`) The map of service names and their
    #: mapped domain name format strings
    #: e.g. ``{'web': '{feature:branch-,pull-}.test.example.com.'}``.
    route53_records = {}

    def __init__(self, **values):
        # Pop and set "name" and "ec2_connection" first because other
        # properties require it.
        try:
            self.name = values.pop('name')
        except KeyError:
            raise TypeError('missing name parameter')
        else:
            if not isinstance(self.name, basestring):
                raise TypeError('name must be a string, not ' +
                                repr(self.name))
        try:
            self.ec2_connection = values.pop('ec2_connection')
        except KeyError:
            raise TypeError('missing ec2_connection parameter')
        else:
            if not isinstance(self.ec2_connection, EC2Connection):
                raise TypeError('ec2_connection must be an instance of '
                                'boto.ec2.connection.EC2Connection, not ' +
                                repr(self.ec2_connection))
        # For all keys: self.$key = values['$key']
        for attr, value in values.iteritems():
            setattr(self, attr, value)
        if self.private_key is None:
            self.key_pair

    @property
    def private_key(self):
        """(:class:`paramiko.pkey.PKey`) The pair of public and private key."""
        return getattr(self, '_private_key', None)

    @private_key.setter
    def private_key(self, pkey):
        if not isinstance(pkey, PKey):
            raise TypeError('private_key must be an instance of paramiko.'
                            'pkey.PKey, not ' + repr(pkey))
        self._private_key = pkey
        self._create_github_deploy_key()
        keys = self.ec2_connection.get_all_key_pairs([self.key_name])
        if keys:
            key_pair = keys[0]
        else:
            key_pair = self.ec2_connection.import_key_pair(
                self.key_name,
                self.public_key_string
            )
        self._key_pair = key_pair

    @property
    def public_key_string(self):
        """(:class:`basestring`) The public key string."""
        elements = (self.private_key.get_name(),
                    self.private_key.get_base64(), self.key_name)
        return ' '.join(elements)

    @property
    def key_pair(self):
        """(:class:`boto.ec2.keypair.KeyPair`) The EC2 key pair matched to
        :attr:`private_key`.

        """
        try:
            return self._key_pair
        except AttributeError:
            self._key_pair = self.ec2_connection.create_key_pair(
                self.key_name
            )
            private_key = str(self._key_pair.material)
            self._private_key = RSAKey.from_private_key(io.BytesIO(private_key))
            self._create_github_deploy_key()

    @property
    def key_name(self):
        """(:class:`basestring`) The human-readable title of the key pair."""
        return self.KEY_PAIR_NAME_FORMAT.format(app=self)

    @cached_property
    def github(self):
        """(:class:`github3.GitHub <github3.github.GitHub>`) The GitHub
        connection.

        """
        gh = GitHub()
        gh._session = self.repository._session
        return gh

    @property
    def repository(self):
        """(:class:`github3.repos.Repository`) The repository of the app."""
        return getattr(self, '_repository', None)

    @repository.setter
    def repository(self, repos):
        if not isinstance(repos, Repository):
            raise TypeError('repository must be an instance of github3.repos.'
                            'Repository, not ' + repr(repos))
        self._repository = repos
        if hasattr(self, '_private_key'):
            self._create_github_deploy_key()

    def _create_github_deploy_key(self):
        try:
            repos = self._repository
        except AttributeError:
            pass
        else:
            actual_key = self.private_key.get_base64()
            for key in repos.list_keys():
                if key.title != self.key_name:
                    continue
                elif key.key.split()[1] != actual_key:
                    continue
                break
            else:
                repos.create_key(self.key_name, self.public_key_string)

    @cached_property
    def route53_connection(self):
        """(:class:`boto.route53.connection.Route53Connection`)
        The Route 53 connection.

        """
        ec2 = self.ec2_connection
        return Route53Connection(
            aws_access_key_id=ec2.provider.access_key,
            aws_secret_access_key=ec2.provider.secret_key,
            port=ec2.port,
            proxy=ec2.proxy, proxy_port=ec2.proxy_port,
            debug=ec2.debug,
            security_token=ec2.provider.security_token,
            validate_certs=ec2.https_validate_certificates
        )

    def __repr__(self):
        c = type(self)
        return '<{0}.{1} {2!r}>'.format(c.__module__, c.__name__, self.name)
