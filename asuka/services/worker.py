""":mod:`asuka.services.worker` --- Worker
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import pipes

from ..service import Service

__all__ = 'CeleryService',


class CeleryService(Service):

    @property
    def required_python_packages(self):
        packages = set(super(CeleryService, self).required_python_packages)
        packages.add('celery >= 3.0.0')
        return packages

    @property
    def options(self):
        for key, value in self.config.items():
            yield '--' + key
            if value is not True:
                yield str(value)

    def install(self, instance):
        super(CeleryService, self).install(instance)
        format_args = {
            'app_name': instance.app.name,
            'service_name': self.name,
            'service_path': instance.app.name + '/' + self.name,
            'upstart_name': instance.app.name + '-' + self.name,
            'options': ' '.join(pipes.quote(v) for v in self.options)
        }
        instance.write_file(
            '/etc/init/{upstart_name}.conf'.format(**format_args),
            '''\
description "{app_name} {service_name} service"

start on runlevel [2345]
stop on runlevel [06]

env PYTHONPATH="/etc/{service_path}"

pre-start script
    mkdir -p -m0777 /var/run/{app_name} /var/log/{service_path}
    chown {app_name}:{app_name} /var/run/{app_name} /var/log/{app_name}
end script

script
    exec celery worker {options}
end script

post-stop script
    rm -f /var/run/{service_path}.pid
end script

# vim: set et sw=4 ts=4 sts=4
'''.format(**format_args),
            sudo=True
        )
        instance.sudo([
            'service', instance.app.name + '-' + self.name, 'start'
        ])
