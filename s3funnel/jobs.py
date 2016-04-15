# jobs.py - Collection of workerpool Job objects
# Copyright (c) 2008 Andrey Petrov
#
# This module is part of s3funnel and is released under
# the MIT license: http://www.opensource.org/licenses/mit-license.php

from workerpool import Job
import boto
import time
try:
    import hashlib
except ImportError:
    import md5 as hashlib

import os
import logging
log = logging.getLogger(__name__)

READ_CHUNK = 8192

# Various exceptions we're expecting
from boto.exception import BotoServerError, BotoClientError, S3ResponseError
from httplib import IncompleteRead
from socket import error as SocketError
from s3funnel import FunnelError

class JobError(Exception):
    pass

# Jobs

class GetJob(Job):
    "Download the given key from S3."
    def __init__(self, bucket, key, failed, config={}):
        log.info
        self.bucket = bucket
        self.failed = failed
        self.retries = config.get('retry', 5)
        self.ignore_s3fs_dirs = config.get('ignore_s3fs_dirs',True)
        self.dry_run = config.get('dry_run')
        if key.strip() and (' ' in key):
            self.key = key
            self.key = key.split(' ')[0]
            self.ver = key.split(' ')[1]
        else:
            self.key = "null"
            self.ver = "null"
            log.error("Wrong format for key: %s" % key)
           # return

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                b = toolbox.get_bucket(self.bucket)

                b.connection.provider.metadata_prefix = ''
                k = b.get_key(self.key)
                m = k.get_metadata('content-type')

                if m == 'application/x-directory' and self.ignore_s3fs_dirs:
                     log.warn("Skipping s3fs directory: %s" % self.key)
                     return
                try:
                    # Create directories in case key has "/"
                    if not self.dry_run and os.path.dirname(self.key) and not os.path.exists(os.path.dirname(self.key)):
                        os.makedirs(os.path.dirname(self.key))
                except OSError:
                    pass
                if self.dry_run:
                    log.info("DRY RUN: GET s3://%s/%s", self.bucket, self.key)
                    return
                # Note: This creates a file, even if the download fails
                if self.key != 'null':
                    if self.ver == 'null':
                        k.get_contents_to_filename(self.key)
                        log.info("GET s3://%s/%s", self.bucket, self.key)
                    else:
                        k.get_contents_to_filename(self.key, version_id=self.ver)
                        log.info("GET s3://%s/%s with version: %s", self.bucket, self.key, self.ver)
                return
            except S3ResponseError, e:
                if e.status == 404:
                    log.error("Not found: %s" % self.key)
                    raise
                else:
                    log.warning("Connection lost, reconnecting and retrying...")
                    toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0) # Exponential backoff
            except IOError, e:
                log.error("%s: '%s'" % (e.strerror, e.filename))
                raise

        log.error("Failed to get: %s" % self.key)
        raise JobError()

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            os.unlink(self.key) # Remove file since download failed
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class LookupJob(Job):
    """Lookup the given key. Checks accessibility of the key"""

    def __init__(self, bucket, key, failed, config):
        self.bucket = bucket
        self.key = key
        self.failed = failed
        self.retries = config.get('retry', 5)

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                if toolbox.get_bucket(self.bucket).lookup(self.key):
                    log.info("OK: %s" % self.key)
                    return
            except S3ResponseError, e:
                if e.status == 403 or e.status == 404:
                    log.error("%s on %s" % (e.status, self.key))
                    return
                else:
                    log.warning("Connection lost, reconnecting and retrying...")
                    toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0)  # Exponential backoff

        log.error("Failed to lookup: %s" % self.key)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class PutJob(Job):
    "Upload the given file to S3, where the key corresponds to basename(path)"
    def __init__(self, bucket, path, failed, config={}):
        self.bucket = bucket
        self.path = path
        self.failed = failed
        self.key = self.path
        if not config.get('put_full_path'):
            self.key = os.path.basename(self.key)
        # --del-prefix logic
        self.del_prefix = config.get('del_prefix')
        if self.del_prefix and self.key.startswith(self.del_prefix):
            self.key = self.key.replace(self.del_prefix, '', 1)
        # --add-prefix logic
        self.add_prefix = config.get('add_prefix', '')
        self.key = "%s%s" % (self.add_prefix, self.key)
        self.retries = config.get('retry', 5)
        self.only_new = config.get('put_only_new')
        self.headers = config.get('headers', {})
        acl = config.get('acl')
        if acl not in ['private', 'public-read', 'public-read-write', 'authenticated-read']:
            log.warning("Bad ACL `%s` for key, setting to `private`: %s" % (self.acl, self.key))
            acl = 'private'
        self.headers['x-amz-acl'] = acl
        self.dry_run = config.get('dry_run')

    def _is_new(self, bucket, key):
        # Get existing key etag
        k = bucket.get_key(key)
        if not k: return True
        etag = k.etag[1:-1]

        # Compute file md5
        fp = open(self.path, 'rb')
        hash = hashlib.md5()
        data = fp.read(READ_CHUNK)
        while data:
            hash.update(data)
            data = fp.read(READ_CHUNK)
        fp.close()
        digest = hash.hexdigest()

        return etag != digest

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                bucket = toolbox.get_bucket(self.bucket)
                if self.only_new and not self._is_new(bucket, self.key):
                    log.info("Already exists, skipped: s3://%s/%s", self.bucket, self.key)
                    return
                if self.dry_run:
                    log.info("DRY RUN: PUT %s to s3://%s/%s", self.path, self.bucket, self.key)
                    return
                log.info("PUT %s to s3://%s/%s", self.path, self.bucket, self.key)
                k = bucket.new_key(self.key)
                k.set_contents_from_filename(self.path, self.headers)
                return
            except S3ResponseError, e:
                log.warning("Connection lost, reconnecting and retrying...")
                toolbox.reset()
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0) # Exponential backoff
            except IOError, e:
                log.warning("Path does not exist, skipping: %s" % self.path)
                break
            except Exception, e:
                log.critical("Unexpected exception: %r" % e)
                break

        log.error("Failed to put: %s" % self.key)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class DeleteJob(Job):
    "Delete the given key from S3."
    def __init__(self, bucket, key, failed, config={}):
        self.bucket = bucket
        self.key = key
        self.failed = failed
        self.retries = config.get('retry', 5)
        self.dry_run = config.get('dry_run')

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                if self.dry_run:
                    log.info("DRY RUN: DELETE s3://%s/%s", self.bucket, self.key)
                    return
                log.info("DELETE s3://%s/%s", self.bucket, self.key)
                toolbox.get_bucket(self.bucket).delete_key(self.key)
                return
            except S3ResponseError, e:
                log.warning("Connection lost, reconnecting and retrying...")
                toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0) # Exponential backoff

        log.error("Failed to delete: %s" % self.key)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class DeleteMultipleJob(Job):
    "Delete the given keys from S3."

    def __init__(self, bucket, keys, failed, config={}):
        self.bucket = bucket
        self.keys = keys
        self.failed = failed
        self.retries = config.get('retry', 5)

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                k = toolbox.get_bucket(self.bucket).delete_keys(self.keys)
                if hasattr(k, 'errors') and k.errors:
                    log.error("Failed to delete: %s" % self.keys)
                else:
                    log.info("Deleted: %s" % self.keys)
                return
            except S3ResponseError, e:
                log.warning("Connection lost, reconnecting and retrying...")
                toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0)  # Exponential backoff
            finally:
                pass

        log.error("Failed to delete: %s" % self.keys)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class CopyJob(Job):
    "Copy the given key from another bucket."
    def __init__(self, bucket, key, failed, config={}):
        self.bucket = bucket
        self.key = key
        # --add-prefix logic
        self.add_prefix = config.get('add_prefix', '')
        self.dest_key = "%s%s" % (self.add_prefix, key)
        # --del-prefix logic
        self.del_prefix = config.get('del_prefix')
        if self.del_prefix and self.dest_key.startswith(self.del_prefix):
            self.dest_key = self.dest_key.replace(self.del_prefix, '', 1)
        self.source_bucket = config.get('source_bucket')
        self.failed = failed
        self.retries = config.get('retry', 5)
        self.dry_run = config.get('dry_run')

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                if self.dry_run:
                    log.info("DRY RUN: COPY s3://%s/%s to s3://%s/%s", self.source_bucket, self.key, self.bucket, self.key)
                    return
                log.info("COPY s3://%s/%s to s3://%s/%s", self.source_bucket, self.key, self.bucket, self.key)
                toolbox.get_bucket(self.bucket).copy_key(self.dest_key, self.source_bucket, self.key)
                return
            except S3ResponseError, e:
                log.warning("Connection lost, reconnecting and retrying...")
                toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0) # Exponential backoff

        log.error("Failed to copy: %s" % self.key)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class SetAclJob(Job):
    "Copy the given key from another bucket."

    def __init__(self, bucket, key, failed, config={}):
        self.bucket = bucket
        self.key = key
        self.failed = failed
        self.retries = config.get('retry', 5)

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                k = toolbox.get_conn().make_request(method='HEAD', bucket=self.bucket, key=self.key)
                if k.status == 200:
                    log.info("Done: %s" % self.key)
                else:
                    log.error("%s on %s" % (k.status, self.key))
                    raise BotoServerError
                return
            except S3ResponseError, e:
                log.warning("Connection lost, reconnecting and retrying...")
                toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0)  # Exponential backoff

        log.error("Failed to copy: %s" % self.key)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)

class CheckAclJob(Job):
    "Copy the given key from another bucket."

    aws_services_owner = 'db6a261a5c90f39366dde55bd72b8db7c7ae729538e8694142b8b68fe2348bdf'

    def __init__(self, bucket, key, failed, config={}):
        self.bucket = bucket
        self.key = key
        self.failed = failed
        self.retries = config.get('retry', 5)

    def _do(self, toolbox):
        for i in xrange(self.retries):
            try:
                ok = True
                x = toolbox.get_bucket(self.bucket).get_acl(key_name=self.key)
                if not x.owner.id == self.aws_services_owner:
                    print "%s Owner wrong: %s" % (self.key, x.owner.display_name)
                    ok = False
                elif len(x.acl.grants) > 0:
                    for acl in x.acl.grants:
                        if not (acl.id == self.aws_services_owner and acl.permission == 'FULL_CONTROL'):
                            print "%s has grant for %s" % (self.key, x.acl.grants[acl].display_name)
                            ok = False
                if ok:
                    print "OK: %s" % self.key
                return
            except S3ResponseError, e:
                if e.status == 404:
                    log.warning("%s not found" % self.key)
                    return
                elif e.status == 403:
                    log.warning("%s access denied" % self.key)
                    return
                else:
                    log.warning("Connection lost, reconnecting and retrying...")
                    toolbox.reset()
            except BotoServerError, e:
                break
            except (IncompleteRead, SocketError, BotoClientError), e:
                log.warning("Caught exception: %r.\nRetrying..." % e)
                time.sleep((2 ** i) / 4.0)  # Exponential backoff

        log.error("Failed to copy: %s" % self.key)

    def run(self, toolbox):
        try:
            self._do(toolbox)
        except JobError, e:
            self.failed.put(self.key)
        except Exception, e:
            self.failed.put(e)
