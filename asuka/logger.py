""":mod:`asuka.logger` --- Utilities for using :mod:`logging`
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

"""
import logging

__all__ = 'LoggerProviderMixin',


class LoggerProviderMixin(object):
    """The simple mixin that provides convenient :meth:`get_logger()`
    method.

    """

    def get_logger_name(self, method_name=None):
        """Makes the respective name for the logger of the object, or
        the method if it's present.

        :param method_name: an optional method name to get logger name
        :type method_name: :class:`basestring`
        :returns: the logger name
        :rtype: :class:`basestring`

        """
        fmt = '{0.__module__}.{0.__name__}'
        if method_name:
            fmt += '.{1}'
        return fmt.format(type(self), method_name)

    def get_logger(self, method_name=None):
        """Gets the respective logger of the object, or the method
        if it's present.

        :param method_name: an optional method name to get logger name
        :type method_name: :class:`basestring`
        :returns: the logger
        :rtype: :class:`logging.Logger`

        """
        return logging.getLogger(self.get_logger_name(method_name))
