"""Boardfarm core plugin."""

from argparse import ArgumentParser, Namespace
from typing import Dict, List

from pluggy import PluginManager

from boardfarm import hookimpl
from boardfarm.devices.base_devices import BoardfarmDevice
from boardfarm.lib.boardfarm_pexpect import BoardfarmPexpect
from boardfarm.lib.device_manager import DeviceManager
from boardfarm.plugins.hookspecs import devices


@hookimpl
def boardfarm_add_hookspecs(plugin_manager: PluginManager) -> None:
    """Add boardfarm core plugin hookspecs.

    :param plugin_manager: plugin manager
    """
    plugin_manager.add_hookspecs(devices)


@hookimpl
def boardfarm_add_cmdline_args(argparser: ArgumentParser) -> None:
    """Add boardfarm command line arguments.

    :param argparser: argument parser
    """
    argparser.add_argument("--board-name", required=True, help="Board name")
    argparser.add_argument(
        "--env-config", required=True, help="Environment JSON config file path"
    )
    argparser.add_argument(
        "--inventory-config", required=True, help="Inventory JSON config file path"
    )


@hookimpl
def boardfarm_cmdline_parse(
    argparser: ArgumentParser, cmdline_args: List[str]
) -> Namespace:
    """Parse command line arguments.

    :param argparser: argument parser instance
    :param cmdline_args: command line arguments list
    :returns: command line arguments
    """
    return argparser.parse_args(args=cmdline_args)


def _get_device_name_from_user(
    devices_dict: Dict[str, BoardfarmDevice], exit_code: str
) -> str:
    """Get device name from user."""
    print("----------------------------------------------")
    print("ID  Device name      Device type      Consoles")
    print("----------------------------------------------")
    device_names = sorted(list(devices_dict.keys()))
    for i, device_name in enumerate(device_names, start=1):
        device = devices_dict.get(device_name)
        num_consoles = len(device.get_interactive_consoles())
        print(f"{i: >2}  {device_name: <16} {device.device_type: <16} {num_consoles}")
    print(f"\nEnter '{exit_code}' to exit the interactive shell.\n")
    device_name = None
    device_id = input("Please enter a device ID: ")
    if device_id == exit_code:
        device_name = device_id
    elif not device_id.isdigit():
        print("\nERROR: Wrong input. Please try again.\n")
    elif int(device_id) > len(device_names):
        print("\nERROR: Wrong device ID. Please try again.\n")
    else:
        device_name = device_names[int(device_id) - 1]
    return device_name


def _get_console_name_from_user(
    device_name: str, consoles: Dict[str, BoardfarmPexpect]
) -> str:
    """Get device console name from user."""
    console_name = None
    console_names = sorted(list(consoles.keys()))
    while True:
        if not console_names:
            print(
                f"\nERROR: No console available for {device_name}. "
                "Please try another device.\n"
            )
            break
        if len(console_names) == 1:
            console_name = console_names[0]
            break
        print(f"{device_name}' has more than one console.")
        console_ids = " ".join(
            [f"{x}) {y:16}" for x, y in enumerate(console_names, start=1)]
        )
        print(f"\n{console_ids} q) go back.\n")
        console_id = input("Please enter a console ID: ")
        if not console_id.isdigit():
            print("\nERROR: Wrong input. Please try again.\n")
            continue
        if int(console_id) > len(console_names):
            print("\nERROR: Wrong console ID. Please try again.\n")
            continue
        console_name = console_names[int(console_id) - 1]
        break
    return console_name


@hookimpl(trylast=True)
def boardfarm_post_deploy_devices(device_manager: DeviceManager) -> None:
    """Enter into boardfarm interactive session after deployment.

    :param device_manager: device manager
    """
    print("----------------------------------------------\n")
    print("         BOARDFARM INTERACTIVE SHELL\n")
    exit_code = "q"
    while True:
        devices_dict = device_manager.get_devices_by_type(BoardfarmDevice)
        if not devices_dict:
            print("No device available in the environment.")
            break
        device_name = _get_device_name_from_user(devices_dict, exit_code)
        if device_name == exit_code:
            break
        if device_name is None:
            continue
        device = devices_dict.get(device_name)
        consoles = device.get_interactive_consoles()
        console_name = _get_console_name_from_user(device_name, consoles)
        if console_name is None:
            continue
        print(f"\nEntering into {console_name}({device_name})\n")
        selected_console = consoles.get(console_name)
        selected_console.start_interactive_session()
        print("\n")
    print("Bye.")
