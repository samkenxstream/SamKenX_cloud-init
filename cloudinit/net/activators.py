# This file is part of cloud-init. See LICENSE file for license information.
import logging
import os
from abc import ABC, abstractmethod
from typing import Iterable, List, Type

from cloudinit import subp
from cloudinit import util
from cloudinit.net.eni import available as eni_available
from cloudinit.net.netplan import available as netplan_available
from cloudinit.net.network_state import NetworkState
from cloudinit.net.sysconfig import NM_CFG_FILE


LOG = logging.getLogger(__name__)


def _alter_interface(cmd, device_name):
    LOG.debug("Attempting command %s for device %s", cmd, device_name)
    try:
        (_out, err) = subp.subp(cmd)
        if len(err):
            LOG.warning("Running %s resulted in stderr output: %s",
                        cmd, err)
        return True
    except subp.ProcessExecutionError:
        util.logexc(LOG, "Running interface command %s failed", cmd)
        return False


class NetworkActivator(ABC):
    @staticmethod
    @abstractmethod
    def available() -> bool:
        raise NotImplementedError()

    @staticmethod
    @abstractmethod
    def bring_up_interface(device_name: str) -> bool:
        raise NotImplementedError()

    @staticmethod
    @abstractmethod
    def bring_down_interface(device_name: str) -> bool:
        raise NotImplementedError()

    @classmethod
    def bring_up_interfaces(cls, device_names: Iterable[str]) -> bool:
        return all(cls.bring_up_interface(device) for device in device_names)

    @classmethod
    def bring_up_all_interfaces(cls, network_state: NetworkState) -> bool:
        return cls.bring_up_interfaces(
            [i['name'] for i in network_state.iter_interfaces()]
        )

    @classmethod
    def bring_down_interfaces(cls, device_names: Iterable[str]) -> bool:
        return all(cls.bring_down_interface(device) for device in device_names)

    @classmethod
    def bring_down_all_interfaces(cls, network_state: NetworkState) -> bool:
        return cls.bring_down_interfaces(
            [i['name'] for i in network_state.iter_interfaces()]
        )


class IfUpDownActivator(NetworkActivator):
    # Note that we're not overriding bring_up_interfaces to pass something
    # like ifup --all because it isn't supported everywhere.
    # E.g., NetworkManager has a ifupdown plugin that requires the name
    # of a specific connection.
    @staticmethod
    def available(target=None) -> bool:
        """Return true if ifupdown can be used on this system."""
        return eni_available(target=target)

    @staticmethod
    def bring_up_interface(device_name: str) -> bool:
        """Bring up interface using ifup."""
        cmd = ['ifup', device_name]
        return _alter_interface(cmd, device_name)

    @staticmethod
    def bring_down_interface(device_name: str) -> bool:
        cmd = ['ifdown', device_name]
        return _alter_interface(cmd, device_name)


class NetworkManagerActivator(NetworkActivator):
    @staticmethod
    def available(target=None) -> bool:
        """ Return true if network manager can be used on this system."""
        config_present = os.path.isfile(
            subp.target_path(target, path=NM_CFG_FILE)
        )
        nmcli_present = subp.which('nmcli', target=target)
        return config_present and bool(nmcli_present)

    @staticmethod
    def bring_up_interface(device_name: str) -> bool:
        cmd = ['nmcli', 'connection', 'up', device_name]
        return _alter_interface(cmd, device_name)

    @staticmethod
    def bring_down_interface(device_name: str) -> bool:
        cmd = ['nmcli', 'connection', 'down', device_name]
        return _alter_interface(cmd, device_name)


class NetplanActivator(NetworkActivator):
    NETPLAN_CMD = ['netplan', 'apply']

    @staticmethod
    def available(target=None) -> bool:
        """ Return true if netplan can be used on this system."""
        return netplan_available(target=target)

    @staticmethod
    def bring_up_interface(device_name: str) -> bool:
        LOG.debug("Calling 'netplan apply' rather than "
                  "altering individual interfaces")
        return _alter_interface(NetplanActivator.NETPLAN_CMD, 'all')

    @staticmethod
    def bring_up_interfaces(device_names: Iterable[str]) -> bool:
        LOG.debug("Calling 'netplan apply' rather than "
                  "altering individual interfaces")
        return _alter_interface(NetplanActivator.NETPLAN_CMD, 'all')

    @staticmethod
    def bring_up_all_interfaces(network_state: NetworkState) -> bool:
        return _alter_interface(NetplanActivator.NETPLAN_CMD, 'all')

    @staticmethod
    def bring_down_interface(device_name: str) -> bool:
        LOG.debug("Calling 'netplan apply' rather than "
                  "altering individual interfaces")
        return _alter_interface(NetplanActivator.NETPLAN_CMD, 'all')

    @staticmethod
    def bring_down_interfaces(device_names: Iterable[str]) -> bool:
        LOG.debug("Calling 'netplan apply' rather than "
                  "altering individual interfaces")
        return _alter_interface(NetplanActivator.NETPLAN_CMD, 'all')

    @staticmethod
    def bring_down_all_interfaces(network_state: NetworkState) -> bool:
        return _alter_interface(NetplanActivator.NETPLAN_CMD, 'all')


# This section is mostly copied and pasted from renderers.py. An abstract
# version to encompass both seems overkill at this point
DEFAULT_PRIORITY = [
    IfUpDownActivator,
    NetworkManagerActivator,
    NetplanActivator,
]


def search_activator(
    priority=None, target=None
) -> List[Type[NetworkActivator]]:
    if priority is None:
        priority = DEFAULT_PRIORITY

    unknown = [i for i in priority if i not in DEFAULT_PRIORITY]
    if unknown:
        raise ValueError(
            "Unknown activators provided in priority list: %s" % unknown)

    return [activator for activator in priority if activator.available(target)]


def select_activator(priority=None, target=None) -> Type[NetworkActivator]:
    found = search_activator(priority, target)
    if not found:
        if priority is None:
            priority = DEFAULT_PRIORITY
        tmsg = ""
        if target and target != "/":
            tmsg = " in target=%s" % target
        raise RuntimeError(
            "No available network activators found%s. Searched "
            "through list: %s" % (tmsg, priority))
    return found[0]