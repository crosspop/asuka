""":mod:`asuka.urls` --- URL resolver
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""

__all__ = 'repository', 'service_config_file'


def repository(app):
    """GitHub repository."""
    return app.repository.html_url


def service_config_file(deployment, service):
    """The :file:`{service}.yml` config file of the commit."""
    branch = deployment.branch
    commit = deployment.commit
    path = branch.app.config_dir
    if not path.endswith('/'):
        path += '/'
    path += service + '.yml'
    return branch.repository.contents(path, ref=commit.ref).html_url
