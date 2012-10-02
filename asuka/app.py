""":mod:`asuka.app` --- Application configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import io

from boto.ec2.connection import EC2Connection
from paramiko.pkey import PKey
from paramiko.rsakey import RSAKey

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
        name = self.KEY_PAIR_NAME_FORMAT.format(app=self)
        keys = self.ec2_connection.get_all_key_pairs([name])
        if keys:
            key_pair = keys[0]
        else:
            pub = ' '.join((pkey.get_name(), pkey.get_base64(), name))
            key_pair = self.ec2_connection.import_key_pair(name, pub)
        self._key_pair = key_pair

    @property
    def key_pair(self):
        """(:class:`boto.ec2.keypair.KeyPair`) The EC2 key pair matched to
        :attr:`private_key`.

        """
        try:
            return self._key_pair
        except AttributeError:
            self._key_pair = self.ec2_connection.create_key_pair(
                self.KEY_PAIR_NAME_FORMAT.format(app=self)
            )
            private_key = str(self._key_pair.material)
            self._private_key = RSAKey.from_private_key(io.BytesIO(private_key))
