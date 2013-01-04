""":mod:`asuka.services.pgsql` --- PostgreSQL
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
from ..service import Service

__all__ = 'PostgreSQLService',


class PostgreSQLService(Service):

    @property
    def required_apt_packages(self):
        base = super(PostgreSQLService, self).required_apt_packages
        packages = frozenset(['python-psycopg2', 'postgresql-client'])
        return packages.union(base)

    @property
    def master_database(self):
        try:
            return self.config['master_database']
        except KeyError:
            return self.app.repository.master_branch.replace('-', '_')

    @property
    def database(self):
        try:
            fmt = self.config['database_format']
        except KeyError:
            return self.branch.label_
        return fmt.format(branch=self.branch)

    @property
    def connection_info(self):
        cfg = self.config
        kw_list = 'user', 'host', 'unix_sock', 'port', 'password'
        return dict((kw, cfg[kw]) for kw in kw_list if kw in cfg)

    def install(self, instance):
        cfg = self.config
        if self.database == self.master_database:
            template = 'template1'
        else:
            # TODO: follow base if pull request
            template = self.master_database
        config_opt_map = dict(
            host='host',
            user='username',
            password='password',
            encoding='encoding',
            lc_collate='lc-collate',
            lc_ctype='lc-ctype',
            tablespace='tablespace'
        )
        options = ['--' + opt + '=' + str(cfg[cfg_name])
                   for cfg_name, opt in config_opt_map.iteritems()
                   if cfg_name in cfg]
        fail = instance.do(['createdb', '--template', template] + options +
                           [self.database])
        if fail:
            # Workaround for:
            # ERROR:  source database "..." is being accessed by other users
            instance.do([
                'pg_dump', '-h', str(cfg['host']), '-U', str(cfg['user']),
                '-E', str(cfg['encoding']), '-f', '/tmp/' + template + '.sql',
                template
            ])
            instance.do(['createdb', '--template', 'template1'] + options +
                        [self.database])
            instance.do([
                'psql', '-h', str(cfg['host']), '-U', str(cfg['user']),
                '-f', '/tmp/' + template + '.sql', self.database
            ])
        info = self.connection_info
        info['database'] = self.database
        return info
