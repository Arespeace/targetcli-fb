'''
Implements the targetcli backstores related UI.

This file is part of targetcli.
Copyright (c) 2011-2013 by Datera, Inc

Licensed under the Apache License, Version 2.0 (the "License"); you may
not use this file except in compliance with the License. You may obtain
a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
License for the specific language governing permissions and limitations
under the License.
'''

import os
from ui_node import UINode, UIRTSLibNode
from rtslib import RTSRoot
from rtslib import FileIOBackstore, IBlockBackstore
from rtslib import PSCSIBackstore, RDDRBackstore, RDMCPBackstore
from rtslib import FileIOStorageObject, IBlockStorageObject
from rtslib import PSCSIStorageObject, RDDRStorageObject, RDMCPStorageObject
from rtslib.utils import get_block_type, is_disk_partition
from rtslib.utils import convert_human_to_bytes, convert_bytes_to_human
from configshell import ExecutionError

def dedup_so_name(storage_object):
    '''
    Useful for migration from ui_backstore_legacy to new style with
    1:1 hba:so mapping. If name is a duplicate in a backstore, returns
    name_X where X is the HBA index.
    '''
    names = [so.name for so in RTSRoot().storage_objects
             if so.backstore.plugin == storage_object.backstore.plugin]
    if names.count(storage_object.name) > 1:
        return "%s_%d" % (storage_object.name,
                          storage_object.backstore.index)
    else:
        return storage_object.name

class UIBackstores(UINode):
    '''
    The backstores container UI.
    '''
    def __init__(self, parent):
        UINode.__init__(self, 'backstores', parent)
        self.cfs_cwd = "%s/core" % self.cfs_cwd
        self.refresh()

    def refresh(self):
        self._children = set([])
        UIPSCSIBackstore(self)
        UIRDDRBackstore(self)
        UIRDMCPBackstore(self)
        UIFileIOBackstore(self)
        UIIBlockBackstore(self)


class UIBackstore(UINode):
    '''
    A backstore UI.
    Abstract Base Class, do not instantiate.
    '''
    def __init__(self, plugin, parent):
        UINode.__init__(self, plugin, parent)
        self.cfs_cwd = "%s/core" % self.cfs_cwd
        self.refresh()

    def refresh(self):
        self._children = set([])
        for so in RTSRoot().storage_objects:
            if so.backstore.plugin == self.name:
                ui_so = UIStorageObject(so, self)
                ui_so.name = dedup_so_name(so)

    def summary(self):
        no_storage_objects = len(self._children)
        if no_storage_objects > 1:
            msg = "%d Storage Objects" % no_storage_objects
        else:
            msg = "%d Storage Object" % no_storage_objects
        return (msg, None)

    def prm_gen_wwn(self, generate_wwn):
        generate_wwn = \
                self.ui_eval_param(generate_wwn, 'bool', True)
        if generate_wwn:
            self.shell.log.info("Generating a wwn serial.")
        else:
            self.shell.log.info("Not generating a wwn serial.")
        return generate_wwn

    def prm_buffered(self, buffered):
        buffered = \
                self.ui_eval_param(buffered, 'bool', True)
        if buffered:
            self.shell.log.info("Using buffered mode.")
        else:
            self.shell.log.info("Not using buffered mode.")
        return buffered

    def ui_command_delete(self, name):
        '''
        Recursively deletes the storage object having the specified I{name}. If
        there are LUNs using this storage object, they will be deleted too.

        EXAMPLE
        =======
        B{delete mystorage}
        -------------------
        Deletes the storage object named mystorage, and all associated LUNs.
        '''
        self.assert_root()
        try:
            child = self.get_child(name)
        except ValueError:
            self.shell.log.error("No storage object named %s." % name)
        else:
            hba = child.rtsnode.backstore
            child.rtsnode.delete()
            if not list(hba.storage_objects):
                hba.delete()
            self.remove_child(child)
            self.shell.log.info("Deleted storage object %s." % name)
            self.parent.parent.refresh()

    def ui_complete_delete(self, parameters, text, current_param):
        '''
        Parameter auto-completion method for user command delete.
        @param parameters: Parameters on the command line.
        @type parameters: dict
        @param text: Current text of parameter being typed by the user.
        @type text: str
        @param current_param: Name of parameter to complete.
        @type current_param: str
        @return: Possible completions
        @rtype: list of str
        '''
        if current_param == 'name':
            names = [child.name for child in self.children]
            completions = [name for name in names
                           if name.startswith(text)]
        else:
            completions = []

        if len(completions) == 1:
            return [completions[0] + ' ']
        else:
            return completions

    def next_hba_index(self):
        self.shell.log.debug("%r" % [(backstore.plugin, backstore.index)
                                     for backstore in RTSRoot().backstores])
        indexes = [backstore.index for backstore in RTSRoot().backstores
                   if backstore.plugin == self.name]
        self.shell.log.debug("Existing %s backstore indexes: %r"
                             % (self.name, indexes))
        for index in range(1048576):
            if index not in indexes:
                backstore_index = index
                break

        if backstore_index is None:
            raise ExecutionError("Cannot find an available backstore index.")
        else:
            self.shell.log.debug("First available %s backstore index is %d."
                                 % (self.name, backstore_index))
            return backstore_index

    def assert_available_so_name(self, name):
        names = [child.name for child in self.children]
        if name in names:
            raise ExecutionError("Storage object %s/%s already exist."
                                 % (self.name, name))


class UIPSCSIBackstore(UIBackstore):
    '''
    PSCSI backstore UI.
    '''
    def __init__(self, parent):
        UIBackstore.__init__(self, 'pscsi', parent)

    def ui_command_create(self, name, dev):
        '''
        Creates a PSCSI storage object, with supplied name and SCSI device. The
        SCSI device I{dev} can either be a path name to the device, in which
        case it is recommended to use the /dev/disk/by-id hierarchy to have
        consistent naming should your physical SCSI system be modified, or an
        SCSI device ID in the H:C:T:L format, which is not recommended as SCSI
        IDs may vary in time.
        '''
        self.assert_root()
        self.assert_available_so_name(name)
        backstore = PSCSIBackstore(self.next_hba_index(), mode='create')

        if get_block_type(dev) is not None or is_disk_partition(dev):
            self.shell.log.info("Note: block backstore recommended for "
                                "SCSI block devices")

        try:
            so = PSCSIStorageObject(backstore, name, dev)
        except Exception, exception:
            backstore.delete()
            raise exception
        ui_so = UIStorageObject(so, self)
        self.shell.log.info("Created pscsi storage object %s using %s"
                            % (name, dev))
        return self.new_node(ui_so)


class UIRDDRBackstore(UIBackstore):
    '''
    RDDR backstore UI.
    '''
    def __init__(self, parent):
        UIBackstore.__init__(self, 'rd_dr', parent)

    def ui_command_create(self, name, size, generate_wwn=None):
        '''
        Creates an RDDR storage object. I{size} is the size of the ramdisk, and
        the optional I{generate_wwn} parameter is a boolean specifying whether
        or not we should generate a T10 wwn serial for the unit (by default,
        yes).

        SIZE SYNTAX
        ===========
        - If size is an int, it represents a number of bytes.
        - If size is a string, the following units can be used:
            - B{B} or no unit present for bytes
            - B{k}, B{K}, B{kB}, B{KB} for kB (kilobytes)
            - B{m}, B{M}, B{mB}, B{MB} for MB (megabytes)
            - B{g}, B{G}, B{gB}, B{GB} for GB (gigabytes)
            - B{t}, B{T}, B{tB}, B{TB} for TB (terabytes)
        '''
        self.assert_root()
        self.assert_available_so_name(name)
        backstore = RDDRBackstore(self.next_hba_index(), mode='create')
        try:
            so = RDDRStorageObject(backstore, name, size,
                                   self.prm_gen_wwn(generate_wwn))

        except Exception, exception:
            backstore.delete()
            raise exception
        ui_so = UIStorageObject(so, self)
        self.shell.log.info("Created rd_dr ramdisk %s with size %s."
                            % (name, size))
        return self.new_node(ui_so)


class UIRDMCPBackstore(UIBackstore):
    '''
    RDMCP backstore UI.
    '''
    def __init__(self, parent):
        UIBackstore.__init__(self, 'rd_mcp', parent)

    def ui_command_create(self, name, size, generate_wwn=None):
        '''
        Creates an RDMCP storage object. I{size} is the size of the ramdisk,
        and the optional I{generate_wwn} parameter is a boolean specifying
        whether or not we should generate a T10 wwn Serial for the unit (by
        default, yes).

        SIZE SYNTAX
        ===========
        - If size is an int, it represents a number of bytes.
        - If size is a string, the following units can be used:
            - B{B} or no unit present for bytes
            - B{k}, B{K}, B{kB}, B{KB} for kB (kilobytes)
            - B{m}, B{M}, B{mB}, B{MB} for MB (megabytes)
            - B{g}, B{G}, B{gB}, B{GB} for GB (gigabytes)
            - B{t}, B{T}, B{tB}, B{TB} for TB (terabytes)
        '''
        self.assert_root()
        self.assert_available_so_name(name)
        backstore = RDMCPBackstore(self.next_hba_index(), mode='create')
        try:
            so = RDMCPStorageObject(backstore, name, size,
                                    self.prm_gen_wwn(generate_wwn))

        except Exception, exception:
            backstore.delete()
            raise exception
        ui_so = UIStorageObject(so, self)
        self.shell.log.info("Created rd_mcp ramdisk %s with size %s."
                            % (name, size))
        return self.new_node(ui_so)


class UIFileIOBackstore(UIBackstore):
    '''
    FileIO backstore UI.
    '''
    def __init__(self, parent):
        UIBackstore.__init__(self, 'fileio', parent)

    def _create_file(self, filename, size, sparse=True):
        f = open(filename, "w+")
        try:
            if sparse:
                os.ftruncate(f.fileno(), size)
            else:
                self.shell.log.info("Writing %s bytes" % size)
                while size > 0:
                    write_size = min(size, 1024)
                    f.write("\0" * write_size)
                    size -= write_size
        except IOError:
            f.close()
            os.remove(filename)
            raise ExecutionError("Could not expand file to size")
        f.close()

    def ui_command_create(self, name, file_or_dev, size=None,
                          generate_wwn=None, buffered=None, sparse=None):

        '''
        Creates a FileIO storage object. If I{file_or_dev} is a path to a
        regular file to be used as backend, then the I{size} parameter is
        mandatory. Else, if I{file_or_dev} is a path to a block device, the
        size parameter B{must} be ommited. If present, I{size} is the size of
        the file to be used, I{file} the path to the file or I{dev} the path to
        a block device.  The optional I{generate_wwn} parameter is a boolean
        specifying whether or not we should generate a T10 wwn Serial for the
        unit (by default, yes).  The I{buffered} parameter is a boolean stating
        whether or not to enable buffered mode. It is enabled by default
        (asynchronous mode). The I{sparse} parameter is only applicable when
        creating a new backing file. It is a boolean stating if the
        created file should be created as a sparse file (the default), or
        fully initialized.

        SIZE SYNTAX
        ===========
        - If size is an int, it represents a number of bytes.
        - If size is a string, the following units can be used:
            - B{B} or no unit present for bytes
            - B{k}, B{K}, B{kB}, B{KB} for kB (kilobytes)
            - B{m}, B{M}, B{mB}, B{MB} for MB (megabytes)
            - B{g}, B{G}, B{gB}, B{GB} for GB (gigabytes)
            - B{t}, B{T}, B{tB}, B{TB} for TB (terabytes)
        '''
        self.assert_root()
        self.assert_available_so_name(name)
        self.shell.log.debug("Using params size=%s generate_wwn=%s buffered=%s"
                             " sparse=%s"
                             % (size, generate_wwn, buffered, sparse))

        sparse = self.ui_eval_param(sparse, 'bool', True)

        backstore = FileIOBackstore(self.next_hba_index(), mode='create')

        is_dev = get_block_type(file_or_dev) is not None \
                or is_disk_partition(file_or_dev)

        if size is None and is_dev:
            backstore = FileIOBackstore(self.next_hba_index(), mode='create')
            try:
                so = FileIOStorageObject(
                    backstore, name, file_or_dev,
                    gen_wwn=self.prm_gen_wwn(generate_wwn),
                    buffered_mode=self.prm_buffered(buffered))
            except Exception, exception:
                backstore.delete()
                raise exception
            self.shell.log.info("Created fileio %s with size %s."
                                % (name, size))
            self.shell.log.info("Note: block backstore preferred for "
                                " best results.")
            ui_so = UIStorageObject(so, self)
            return self.new_node(ui_so)
        elif size is not None and not is_dev:
            backstore = FileIOBackstore(self.next_hba_index(), mode='create')
            try:
                so = FileIOStorageObject(
                    backstore, name, file_or_dev,
                    size,
                    gen_wwn=self.prm_gen_wwn(generate_wwn),
                    buffered_mode=self.prm_buffered(buffered))
            except Exception, exception:
                backstore.delete()
                raise exception
            self.shell.log.info("Created fileio %s." % name)
            ui_so = UIStorageObject(so, self)
            return self.new_node(ui_so)
        else:
            # use given file size only if backing file does not exist
            if os.path.isfile(file_or_dev):
                new_size = str(os.path.getsize(file_or_dev))
                if size:
                    self.shell.log.info("%s exists, using its size (%s bytes)"
                                        " instead"
                                        % (file_or_dev, new_size))
                size = new_size
            elif os.path.exists(file_or_dev):
                raise ExecutionError("Path %s exists but is not a file" % file_or_dev)
            else:
                # create file and extend to given file size
                if not size:
                    raise ExecutionError("Attempting to create file for new" +
                                         " fileio backstore, need a size")
                self._create_file(file_or_dev, convert_human_to_bytes(size),
                                  sparse)


class UIIBlockBackstore(UIBackstore):
    '''
    IBlock backstore UI.
    '''
    def __init__(self, parent):
        UIBackstore.__init__(self, 'iblock', parent)

    def ui_command_create(self, name, dev, generate_wwn=None):
        '''
        Creates an IBlock Storage object. I{dev} is the path to the TYPE_DISK
        block device to use and the optional I{generate_wwn} parameter is a
        boolean specifying whether or not we should generate a T10 wwn Serial
        for the unit (by default, yes).
        '''
        self.assert_root()
        self.assert_available_so_name(name)
        backstore = IBlockBackstore(self.next_hba_index(), mode='create')
        try:
            so = IBlockStorageObject(backstore, name, dev,
                                     self.prm_gen_wwn(generate_wwn))
        except Exception, exception:
            backstore.delete()
            raise exception
        ui_so = UIStorageObject(so, self)
        self.shell.log.info("Created iblock storage object %s using %s."
                            % (name, dev))
        return self.new_node(ui_so)


class UIStorageObject(UIRTSLibNode):
    '''
    A storage object UI.
    Abstract Base Class, do not instantiate.
    '''
    def __init__(self, storage_object, parent):
        name = storage_object.name
        UIRTSLibNode.__init__(self, name, storage_object, parent)
        self.cfs_cwd = storage_object.path
        self.refresh()

    def ui_command_version(self):
        '''
        Displays the version of the current backstore's plugin.
        '''
        backstore = self.rtsnode.backstore
        self.shell.con.display("Backstore plugin %s %s"
                               % (backstore.plugin, backstore.version))

    def summary(self):
        so = self.rtsnode
        errors = []
        if so.backstore.plugin.startswith("rd"):
            path = "ramdisk"
        else:
            path = so.udev_path

        if not path:
            errors.append("BROKEN STORAGE LINK")

        legacy = []
        if self.rtsnode.name != self.name:
            legacy.append("ADDED SUFFIX")
        if len(list(self.rtsnode.backstore.storage_objects)) > 1:
            legacy.append("SHARED HBA")

        if legacy:
            errors.append("LEGACY: " + ", ".join(legacy))

        size = convert_bytes_to_human(getattr(so, "size", 0))

        if errors:
            msg = ", ".join(errors)
            if path:
                msg += " (%s %s)" % (path, so.status)
            return (msg, False)
        else:
            return ("%s %s%s" % (path, size, so.status), True)

