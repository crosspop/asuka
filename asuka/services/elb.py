""":mod:`asuka.services.elb` --- Elastic Load Balancing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Amazon Web Services`_ `Elastic Load Balancing`_.

.. _Amazon Web Services: http://aws.amazon.com/
.. _Elastic Load Balancing: http://aws.amazon.com/elasticloadbalancing/

"""
from boto.ec2.elb import ELBConnection, HealthCheck, regions
from boto.exception import BotoServerError
from werkzeug.utils import cached_property

from ..service import Service


class ELBService(Service):
    """Elastic Load Balancing."""

    def __init__(self, *args, **kwargs):
        super(ELBService, self).__init__(*args, **kwargs)

    @cached_property
    def elb_connection(self):
        """(:class:`boto.ec2.elb.ELBConnection`) The ELB connection."""
        ec2 = self.app.ec2_connection
        region = next(r for r in regions() if r.name == ec2.region.name)
        return ELBConnection(
            aws_access_key_id=ec2.provider.access_key,
            aws_secret_access_key=ec2.provider.secret_key,
            is_secure=ec2.is_secure, port=ec2.port,
            proxy=ec2.proxy, proxy_port=ec2.proxy_port,
            proxy_user=ec2.proxy_user, proxy_pass=ec2.proxy_pass,
            debug=ec2.debug, region=region,
            security_token=ec2.provider.security_token,
            validate_certs=ec2.https_validate_certificates 
        )

    @property
    def load_balancer_name(self):
        """(:class:`basestring`) The name of load balancer."""
        return '{0}-{1}-{2}'.format(self.app.name, self.name, 'master')

    @property
    def listeners(self):
        """(:class:`list`) The list of configured listeners,
        as a form :meth:`ELBConnection.create_load_balancer_listeners()
        <boto.ec2.elb.ELBConnection.create_load_balancer_listeners>` can
        take.

        """
        return [(l['out_port'], l['in_port'], l['protocol'].upper())
                for l in self.config.get('listeners', [])]

    @cached_property
    def health_check(self):
        """(:class:`boto.ec2.elb.HealthCheck`) The health check
        configuration.

        """
        conf = self.config.get('health_check', {})
        return HealthCheck(**conf)

    @cached_property
    def load_balancer(self):
        """(:class:`boto.ec2.elb.loadbalancer.LoadBalancer`)
        The load balancer object.

        """
        conn = self.elb_connection
        try:
            balancers = conn.get_all_load_balancers([self.load_balancer_name])
        except BotoServerError:
            zones = self.app.ec2_connection.get_all_zones()
            lb = conn.create_load_balancer(
                name=self.load_balancer_name,
                zones=[zone.name for zone in zones],
                listeners=self.listeners
            )
        else:
            lb = balancers[0]
        lb.configure_health_check(self.health_check)
        return lb

    @property
    def required_apt_repositories(self):
        repos = super(ELBService, self).required_apt_repositories
        return repos.union(['ppa:awstools-dev/awstools'])

    @property
    def required_apt_packages(self):
        packages = super(ELBService, self).required_apt_packages
        return packages.union(['elbcli'])

    def install(self, instance):
        super(ELBService, self).install(instance)
        format_args = {
            'service': self,
            'app_name': instance.app.name,
            'service_name': self.name,
            'access_key': self.elb_connection.provider.access_key,
            'secret_key': self.elb_connection.provider.secret_key,
            'region_name': self.elb_connection.region.name,
            'elb_name': self.load_balancer.name
        }
        instance.write_file(
            '/etc/init/{app_name}-{service_name}.conf'.format(**format_args),
            '''\
description "{app_name} {service_name} service"

start on runlevel [2345]
stop on runlevel [06]

script
    elb-register-instances-with-lb "{elb_name}" \
        -I "{access_key}" \
        -S "{secret_key}" \
        --region "{region_name}" \
        --instances `ec2metadata --instance-id`
end script

# vim: set et sw=4 ts=4 sts=4
'''.format(**format_args),
            sudo=True
        )
        instance.sudo([
            'service', instance.app.name + '-' + self.name, 'start'
        ])
