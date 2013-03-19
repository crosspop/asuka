""":mod:`asuka.urls` --- URL resolver
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""

__all__ = 'repository',


def repository(app):
    """GitHub repository."""
    return app.repository.html_url
