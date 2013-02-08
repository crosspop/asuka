""":mod:`asuka.services.elb` --- Elastic Load Balancing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`Amazon Web Services`_ `Elastic Load Balancing`_.

.. _Amazon Web Services: http://aws.amazon.com/
.. _Elastic Load Balancing: http://aws.amazon.com/elasticloadbalancing/

"""
from boto.ec2.elb import ELBConnection, HealthCheck, regions
from boto.exception import BotoServerError
from werkzeug.utils import cached_property

from ..service import DomainService

__all__ = 'ELBService',


class ELBService(DomainService):
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
        return self.config.get(
            'name',
            '{app.name}-{service.name}-{branch.label}'
        ).format(app=self.app, service=self, branch=self.branch)

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
            conn.create_load_balancer(
                name=self.load_balancer_name,
                zones=[zone.name for zone in zones],
                listeners=self.listeners
            )
            balancers = conn.get_all_load_balancers([self.load_balancer_name])
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
        instances = [i.id for i in self.load_balancer.instances]
        instance.sudo([
            'service', instance.app.name + '-' + self.name, 'start'
        ])
        if instances:
            self.load_balancer.deregister_instances(instances)

    @property
    def dns_name(self):
        dns_name = self.load_balancer.dns_name
        assert dns_name is not None
        if not dns_name.endswith('.'):
            dns_name += '.'
        return dns_name

    @property
    def ipv6_dns_name(self):
        return 'ipv6.' + self.dns_name

    @property
    def dualstack_dns_name(self):
        return 'dualstack.' + self.dns_name

    def route_domain(self, name, records):
        zone_id = self.app.route53_hosted_zone_id
        zone = records.connection.get_hosted_zone(zone_id)
        topname = zone['GetHostedZoneResponse']['HostedZone']['Name']
        get_list = records.connection.get_all_rrsets
        if topname == name:
            hosted_zone_id = self.load_balancer.canonical_hosted_zone_name_id
            assert hosted_zone_id is not None
            goals = [('A', self.dns_name), ('AAAA', self.ipv6_dns_name)]
            for type_, dns_name in goals:
                skip = False
                for record in get_list(zone_id, type_, name):
                    if record.type == type_ and record.name == name:
                        if (record.alias_hosted_zone_id == hosted_zone_id and
                            record.alias_dns_name == dns_name):
                            # already exists; skip
                            skip = True
                            break
                        # already exists; delete
                        delete = records.add_change(
                            'DELETE',
                            name=record.name,
                            type=record.type,
                            ttl=record.ttl,
                            alias_hosted_zone_id=hosted_zone_id,
                            alias_dns_name=dns_name,
                            identifier=record.identifier,
                            weight=record.weight,
                            region=record.region
                        )
                        delete.resource_records = record.resource_records
                        break
                if not skip:
                    records.add_change(
                        action='CREATE',
                        name=name,
                        type=type_,
                        alias_hosted_zone_id=hosted_zone_id,
                        alias_dns_name=dns_name
                    )
        else:
            for record in get_list(zone_id, 'CNAME', name):
                if record.type == 'CNAME' and record.name == name:
                    if record.resource_records == [self.dualstack_dns_name]:
                        # already exists; skip
                        return
                    # already exists but not matched; delete first
                    delete = records.add_change(
                        'DELETE',
                        name=record.name,
                        type=record.type,
                        ttl=record.ttl,
                        identifier=record.identifier,
                        weight=record.weight,
                        region=record.region
                    )
                    delete.resource_records = record.resource_records
                    break
            record = records.add_change('CREATE', name, 'CNAME')
            record.add_value(self.dualstack_dns_name)

    def uninstall(self):
        super(ELBService, self).uninstall()
        conn = self.elb_connection
        try:
            balancers = conn.get_all_load_balancers([self.load_balancer_name])
        except BotoServerError:
            pass
        else:
            for balancer in balancers:
                balancer.delete()

    def remove_domain(self, name, records):
        zone_id = self.app.route53_hosted_zone_id
        hosted_zone_id = self.load_balancer.canonical_hosted_zone_name_id
        zone = records.connection.get_hosted_zone(zone_id)
        topname = zone['GetHostedZoneResponse']['HostedZone']['Name']
        get_list = records.connection.get_all_rrsets
        if topname == name:
            goals = [('A', self.dns_name), ('AAAA', self.ipv6_dns_name)]
            for type_, dns_name in goals:
                for record in get_list(zone_id, 'A', name):
                    if record.type == 'A' and record.name == name:
                        delete = records.add_change(
                            'DELETE',
                            name=record.name,
                            type=record.type,
                            ttl=record.ttl,
                            alias_hosted_zone_id=hosted_zone_id,
                            alias_dns_name=dns_name,
                            identifier=record.identifier,
                            weight=record.weight,
                            region=record.region
                        )
                        delete.resource_records = record.resource_records
        else:
            for record in get_list(zone_id, 'CNAME', name):
                if record.type == 'CNAME' and record.name == name:
                    delete = records.add_change(
                        'DELETE',
                        name=record.name,
                        type=record.type,
                        ttl=record.ttl,
                        identifier=record.identifier,
                        weight=record.weight,
                        region=record.region
                    )
                    delete.resource_records = record.resource_records
