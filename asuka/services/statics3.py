""":mod:`asuka.services.statics3` --- Using S3_ for Static Serving
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This service helps static resources like JavaScript, CSS files to be
uploaded to and servced using AWS S3_.

.. _S3: http://aws.amazon.com/s3/

"""
import os
import os.path

from boto.s3.connection import S3Connection
from boto.s3.key import Key
from werkzeug.utils import cached_property

from ..service import Service

__all__ = 'StaticS3Service',


class StaticS3Service(Service):

    @cached_property
    def s3_connection(self):
        """(:class:`boto.s3.conection.S3Connection`) The S3 connection."""
        ec2 = self.app.ec2_connection
        return S3Connection(
            aws_access_key_id=ec2.provider.access_key,
            aws_secret_access_key=ec2.provider.secret_key,
            is_secure=ec2.is_secure, port=ec2.port,
            proxy=ec2.proxy, proxy_port=ec2.proxy_port,
            proxy_user=ec2.proxy_user, proxy_pass=ec2.proxy_pass,
            debug=ec2.debug,
            security_token=ec2.provider.security_token,
            validate_certs=ec2.https_validate_certificates 
        )

    @property
    def static_path(self):
        """(:class:`basestring`) The relative path to the root of
        repository of static files e.g. ``'example/static'``.
        Loaded from the config of the same name.

        """
        return self.config['static_path']

    @property
    def bucket_name(self):
        """(:class:`basestring`) The name of S3 bucket to be uploaded
        files.  Loaded from the config of the same name.

        """
        return self.config['bucket_name']

    @property
    def bucket(self):
        """(:class:`boto.s3.bucket.Bucket`) S3 bucket to be uploaded files."""
        return self.s3_connection.get_bucket(self.bucket_name)

    @property
    def files(self):
        """(:class:`collections.Set`) The set of files to upload.
        It doesn't contain directory names that S3 doesn't need.

        """
        def traverse(path):
            for filename in os.listdir(path):
                fullpath = os.path.join(path, filename)
                if os.path.isdir(fullpath):
                    for sub in traverse(fullpath):
                        yield os.path.join(filename, sub)
                else:
                    yield filename
        with self.branch.fetch(self.commit.ref) as path:
            full_static_path = os.path.join(path, self.static_path)
            return frozenset(traverse(full_static_path))

    @cached_property
    def key_prefix(self):
        """(:class:`basestring`) The prefix for all S3 keys uploaded
        in this build.

        """
        return str(self.commit)

    def upload_files(self):
        logger = self.get_logger('upload_files')
        bucket = self.bucket
        static_path = self.static_path
        with self.branch.fetch(self.commit.ref) as path:
            for filename in self.files:
                key = Key(bucket)
                key.key = '{0}/{1}'.format(self.key_prefix, filename)
                fullname = os.path.join(static_path, filename)
                logger.debug('uploading %r -> %r...', key.key, fullname)
                key.set_contents_from_filename(
                    filename=os.path.join(path, fullname),
                    replace=True,
                    policy='public-read',
                    reduced_redundancy=True
                )

    def install(self, instance):
        super(StaticS3Service, self).install(instance)
        self.upload_files()
        url_base = '//{0}.s3.amazonaws.com/{1}/'.format(
            self.bucket_name,
            self.key_prefix
        )
        return {'url_base': url_base}
