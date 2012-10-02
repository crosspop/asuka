""":mod:`asuka.config` --- Application configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Asuka uses YAML for configuration.  It also automatically updates
the runtime-changed configuration back to the file.

"""
import os.path

from boto.ec2 import connect_to_region
from paramiko.rsakey import RSAKey
from yaml import dump, load

from .app import App

__all__ = 'app_from_config', 'app_from_config_file'


def app_from_config(config):
    """Loads the app from the config mapping.  It returns a pair
    of (app, delta); delta is a dict of minimum dict that should
    be updated.

    For example, the configuration is from the :mod:`dbm`::

        import dbm

        def app_from_dbm(filename):
            config = dbm.open(filename, 'r')
            app, delta = app_from_config(config)
            config.close()
            if delta:
                config = dbm.open(filename, 'w')
                for key, value in delta.iteritems():
                    config[key] = value
                config.close()
            return app

    :param config: the configuration of the app to load
    :type config: :class:`collections.Mapping`
    :returns: the pair of (:class:`~asuka.app.App`,
              :class:`collections.Mapping`)
    :rtype: :class:`tuple`

    """
    app = App(**config)
    delta = {}
    if 'private_key' not in config:
        delta['private_key'] = app.private_key
    return app, delta


def app_from_config_file(filename):
    """Loads the app from the YAML-encoded config file, and updates
    the config file if needed.

    :param filename: the filename of the config to load
    :type filename: :class:`basestring`
    :returns: the loaded app
    :rtype: :class:`~asuka.app.App`

    """
    dirname = os.path.dirname(filename)
    with open(filename) as fp:
        loaded_config = load(fp)
    config = dict(loaded_config)
    config['ec2_connection'] = connect_to_region(**config['ec2_connection'])
    try:
        private_key = config['private_key']
    except KeyError:
        pass
    else:
        private_key = RSAKey.from_private_key_file(
            os.path.join(dirname, private_key)
        )
        config['private_key'] = private_key
    app, delta = app_from_config(config)
    if delta:
        try:
            private_key = delta['private_key']
        except KeyError:
            pass
        else:
            key_filename = app.name + '_id_rsa'
            private_key.write_private_key_file(
                os.path.join(dirname, key_filename)
            )
            delta['private_key'] = key_filename
        loaded_config.update(delta)
        with open(filename, 'w') as fp:
            dump(loaded_config, fp, default_flow_style=False)
    return app
