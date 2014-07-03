# Copyright Hybrid Logic Ltd.  See LICENSE file for details.

"""Tests for :module:`flocker.volume.service`."""

from __future__ import absolute_import

import json
import os
from unittest import skipIf

from zope.interface.verify import verifyObject

from twisted.python.filepath import FilePath, Permissions
from twisted.trial.unittest import TestCase
from twisted.application.service import IService

from ..service import (
    VolumeService, CreateConfigurationError, Volume, DEFAULT_CONFIG_PATH,
    )
from ..filesystems.memory import FilesystemStoragePool
from .._ipc import FakeNode
from ...testtools import skip_on_broken_permissions


class VolumeServiceStartupTests(TestCase):
    """
    Tests for :class:`VolumeService` startup.
    """
    def test_interface(self):
        """:class:`VolumeService` implements :class:`IService`."""
        self.assertTrue(verifyObject(IService,
                                     VolumeService(FilePath(""), None)))

    def test_no_config_UUID(self):
        """If no config file exists in the given path, a new UUID is chosen."""
        service = VolumeService(FilePath(self.mktemp()), None)
        service.startService()
        service2 = VolumeService(FilePath(self.mktemp()), None)
        service2.startService()
        self.assertNotEqual(service.uuid, service2.uuid)

    def test_no_config_written(self):
        """If no config file exists, a new one is written with the UUID."""
        path = FilePath(self.mktemp())
        service = VolumeService(path, None)
        service.startService()
        config = json.loads(path.getContent())
        self.assertEqual({u"uuid": service.uuid, u"version": 1}, config)

    def test_no_config_directory(self):
        """The config file's parent directory is created if it
        doesn't exist."""
        path = FilePath(self.mktemp()).child(b"config.json")
        service = VolumeService(path, None)
        service.startService()
        self.assertTrue(path.exists())

    @skipIf(os.getuid() == 0, "root doesn't get permission errors.")
    @skip_on_broken_permissions
    def test_config_makedirs_failed(self):
        """If creating the config directory fails then CreateConfigurationError
        is raised."""
        path = FilePath(self.mktemp())
        path.makedirs()
        path.chmod(0)
        self.addCleanup(path.chmod, 0o777)
        path = path.child(b"dir").child(b"config.json")
        service = VolumeService(path, None)
        self.assertRaises(CreateConfigurationError, service.startService)

    @skipIf(os.getuid() == 0, "root doesn't get permission errors.")
    @skip_on_broken_permissions
    def test_config_write_failed(self):
        """If writing the config fails then CreateConfigurationError
        is raised."""
        path = FilePath(self.mktemp())
        path.makedirs()
        path.chmod(0)
        self.addCleanup(path.chmod, 0o777)
        path = path.child(b"config.json")
        service = VolumeService(path, None)
        self.assertRaises(CreateConfigurationError, service.startService)

    def test_config(self):
        """If a config file exists, the UUID is loaded from it."""
        path = self.mktemp()
        service = VolumeService(FilePath(path), None)
        service.startService()
        service2 = VolumeService(FilePath(path), None)
        service2.startService()
        self.assertEqual(service.uuid, service2.uuid)


class VolumeServiceAPITests(TestCase):
    """Tests for the ``VolumeService`` API."""

    def test_create_result(self):
        """``create()`` returns a ``Deferred`` that fires with a ``Volume``."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        d = service.create(u"myvolume")
        self.assertEqual(
            self.successResultOf(d),
            Volume(uuid=service.uuid, name=u"myvolume", _pool=pool))

    def test_create_filesystem(self):
        """``create()`` creates the volume's filesystem."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        self.assertTrue(pool.get(volume).get_path().isdir())

    @skip_on_broken_permissions
    def test_create_mode(self):
        """The created filesystem is readable/writable/executable by anyone.

        A better alternative will be implemented in
        https://github.com/ClusterHQ/flocker/issues/34
        """
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        self.assertEqual(pool.get(volume).get_path().getPermissions(),
                         Permissions(0777))

    def test_push_different_uuid(self):
        """Pushing a remotely-owned volume results in a ``ValueError``."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()

        volume = Volume(uuid=u"wronguuid", name=u"blah", _pool=pool)
        self.assertRaises(ValueError, service.push, volume, FakeNode())

    def test_push_destination_run(self):
        """Pushing a locally-owned volume calls ``flocker-volume`` remotely."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        node = FakeNode()

        service.push(volume, node, FilePath(b"/path/to/json"))
        self.assertEqual(node.remote_command,
                         [b"flocker-volume", b"--config", b"/path/to/json",
                          b"receive", volume.uuid.encode("ascii"),
                          b"myvolume"])

    def test_push_default_config(self):
        """Pushing by default calls ``flocker-volume`` with default config
        path."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        node = FakeNode()

        service.push(volume, node)
        self.assertEqual(node.remote_command,
                         [b"flocker-volume", b"--config",
                          DEFAULT_CONFIG_PATH.path,
                          b"receive", volume.uuid.encode("ascii"),
                          b"myvolume"])

    def test_push_writes_filesystem(self):
        """Pushing a locally-owned volume writes its filesystem to the remote
        process."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        filesystem = volume.get_filesystem()
        filesystem.get_path().child(b"foo").setContent(b"blah")
        with filesystem.reader() as reader:
            data = reader.read()
        node = FakeNode()

        service.push(volume, node)
        self.assertEqual(node.stdin.read(), data)

    def test_receive_local_uuid(self):
        """If a volume with same uuid as service is received, ``ValueError`` is
        raised."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()

        self.assertRaises(ValueError, service.receive,
                          service.uuid.encode("ascii"), b"lalala", None)

    def test_receive_creates_volume(self):
        """Receiving creates a volume with the given uuid and name."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        filesystem = volume.get_filesystem()

        with filesystem.reader() as reader:
            service.receive(u"anotheruuid", u"newvolume", reader)
        new_volume = Volume(uuid=u"anotheruuid", name=u"newvolume", _pool=pool)
        d = service.enumerate()

        def got_volumes(volumes):
            self.assertIn(new_volume, volumes)
        d.addCallback(got_volumes)
        return d

    def test_receive_creates_files(self):
        """Receiving creates filesystem with the given push data."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volume = self.successResultOf(service.create(u"myvolume"))
        filesystem = volume.get_filesystem()
        filesystem.get_path().child(b"afile").setContent(b"lalala")

        with filesystem.reader() as reader:
            service.receive(u"anotheruuid", u"newvolume", reader)

        new_volume = Volume(uuid=u"anotheruuid", name=u"newvolume", _pool=pool)
        root = new_volume.get_filesystem().get_path()
        self.assertTrue(root.child(b"afile").getContent(), b"lalala")

    def test_enumerate_no_volumes(self):
        """``enumerate()`` returns no volumes when there are no volumes."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        volumes = self.successResultOf(service.enumerate())
        self.assertEqual([], list(volumes))

    def test_enumerate_some_volumes(self):
        """``enumerate()`` returns all volumes previously ``create()``ed."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        names = {u"somevolume", u"anotherone", u"lastone"}
        expected = {
            self.successResultOf(service.create(name))
            for name in names}
        service2 = VolumeService(FilePath(self.mktemp()), pool)
        service2.startService()
        actual = self.successResultOf(service2.enumerate())
        self.assertEqual(expected, set(actual))

    def test_enumerate_a_volume_with_period(self):
        """``enumerate()`` returns a volume previously ``create()``ed when its
        name includes a period."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        service = VolumeService(FilePath(self.mktemp()), pool)
        service.startService()
        expected = self.successResultOf(service.create(u"some.volume"))
        actual = self.successResultOf(service.enumerate())
        self.assertEqual([expected], list(actual))


class VolumeTests(TestCase):
    """Tests for ``Volume``."""

    def test_equality(self):
        """Volumes are equal if they have the same name, uuid and pool."""
        pool = object()
        v1 = Volume(uuid=u"123", name=u"456", _pool=pool)
        v2 = Volume(uuid=u"123", name=u"456", _pool=pool)
        self.assertTrue(v1 == v2)
        self.assertFalse(v1 != v2)

    def test_inequality_uuid(self):
        """Volumes are unequal if they have different uuids."""
        pool = object()
        v1 = Volume(uuid=u"123", name=u"456", _pool=pool)
        v2 = Volume(uuid=u"123zz", name=u"456", _pool=pool)
        self.assertTrue(v1 != v2)
        self.assertFalse(v1 == v2)

    def test_inequality_name(self):
        """Volumes are unequal if they have different names."""
        pool = object()
        v1 = Volume(uuid=u"123", name=u"456", _pool=pool)
        v2 = Volume(uuid=u"123", name=u"456zz", _pool=pool)
        self.assertTrue(v1 != v2)
        self.assertFalse(v1 == v2)

    def test_inequality_pool(self):
        """Volumes are unequal if they have different pools."""
        v1 = Volume(uuid=u"123", name=u"456", _pool=object())
        v2 = Volume(uuid=u"123", name=u"456", _pool=object())
        self.assertTrue(v1 != v2)
        self.assertFalse(v1 == v2)

    def test_get_filesystem(self):
        """``Volume.get_filesystem`` returns the filesystem for the volume."""
        pool = FilesystemStoragePool(FilePath(self.mktemp()))
        volume = Volume(uuid=u"123", name=u"456", _pool=pool)
        self.assertEqual(volume.get_filesystem(), pool.get(volume))

    def test_container_name(self):
        """The volume's container name adds a ``"flocker-"`` prefix and
        ``"-data"`` suffix.
        """
        volume = Volume(uuid=u"123", name=u"456", _pool=object())
        self.assertEqual(volume._container_name, b"flocker-456-data")