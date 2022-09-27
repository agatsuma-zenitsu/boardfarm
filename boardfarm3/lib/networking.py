"""Boardfarm networking module."""

import re
from collections import defaultdict
from ipaddress import IPv4Address, IPv6Address
from typing import Any, DefaultDict, Optional, Protocol, Union

import jc
import pexpect
from bs4 import BeautifulSoup

from boardfarm3.exceptions import SCPConnectionError, UseCaseFailure
from boardfarm3.lib.parsers.iptables_parser import IptablesParser
from boardfarm3.lib.parsers.nslookup_parser import NslookupParser


class _LinuxConsole(Protocol):
    """Linux console protocol."""

    def execute_command(self, command: str, timeout: int = -1) -> str:
        """Execute given command in the rg-console and return output.

        :param command: command to execute
        :type command: str
        :param timeout: timeout in seconds. Defaults to -1
        :type timeout: int
        """

    def sendline(self, string: str) -> None:
        """Send given string to the console.

        :param string: string to send
        """

    def expect(self, pattern: Union[str, list[str]], timeout: int = -1) -> int:
        """Wait for given regex pattern(s) and return the match index.

        :param pattern: expected regex pattern or pattern list
        :type pattern: Union[str, List[str]]
        :param timeout: timeout in seconds. Defaults to -1
        :type timeout: int
        """

    def expect_exact(self, pattern: Union[str, list[str]], timeout: int = -1) -> int:
        """Wait for given exact pattern(s) and return the match index.

        :param pattern: expected pattern or pattern list
        :type pattern: Union[str, List[str]]
        :param timeout: timeout in seconds. Defaults to -1
        :type timeout: int
        """


def start_tcpdump(
    console: _LinuxConsole,
    interface: str,
    port: Optional[str],
    output_file: str = "pkt_capture.pcap",
    filters: Optional[dict] = None,
    additional_filters: Optional[str] = "",
) -> str:
    """Start tcpdump capture on given interface.

    :param console: console or device instance
    :type console: LinuxConsole
    :param interface: inteface name where packets to be captured
    :type interface: str
    :param port: port number, can be a range of ports(eg: 443 or 433-443)
    :type port: str
    :param output_file: pcap file name, Defaults: pkt_capture.pcap
    :type output_file: str
    :param filters: filters as key value pair(eg: {"-v": "", "-c": "4"})
    :type filters: Optional[Dict]
    :param additional_filters: additional filters
    :type additional_filters: Optional[str]
    :raises ValueError: on failed to start tcpdump
    :return: console ouput and tcpdump process id
    :rtype: str
    """
    command = f"tcpdump -U -i {interface} -n -w {output_file} "
    filter_str = (
        " ".join([" ".join(i) for i in filters.items()]) if filters is not None else ""
    )
    filter_str += additional_filters
    if port:
        output = console.execute_command(f"{command} 'portrange {port}' {filter_str} &")
    else:
        output = console.execute_command(f"{command} {filter_str} &")
    if console.expect_exact([f"tcpdump: listening on {interface}", pexpect.TIMEOUT]):
        raise ValueError(f"Failed to start tcpdump on {interface}")
    return re.search(r"(\[\d{1,10}\]\s(\d{1,6}))", output)[2]


def stop_tcpdump(console: _LinuxConsole, process_id: str) -> None:
    """Stop tcpdump capture.

    :param console: linux console or device instance
    :type console: LinuxConsole
    :param process_id: tcpdump process id
    :type process_id: str
    :raises ValueError: on failed to stop tcpdump process
    """
    console.execute_command(f"kill {process_id}")
    if console.expect_exact(["packets captured", pexpect.TIMEOUT]):
        raise ValueError(f"Failed to stop tcpdump process with PID {process_id}")


def tcpdump_read(
    console: _LinuxConsole,
    capture_file: str,
    protocol: str = "",
    opts: str = "",
    timeout: int = 30,
    rm_pcap: bool = True,
) -> str:
    """Read the given tcpdump and delete the file afterwards.

    :param console: linux device or console instance
    :type console: LinuxConsole
    :param capture_file: pcap file path
    :type capture_file: str
    :param protocol: protocol to the filter
    :type protocol: str
    :param opts: command line options for reading pcap
    :type opts: str
    :param timeout: timeout in seconds for reading pcap
    :type timeout: int
    :param rm_pcap: romove pcap file afterwards
    :type rm_pcap: bool
    :return: tcpdump output
    :rtype: str
    """
    if opts:
        protocol = f"{protocol} and {opts}"
    tcpdump_output = console.execute_command(
        f"tcpdump -n -r {capture_file} {protocol}", timeout=timeout
    )
    if rm_pcap:
        console.execute_command(f"rm {capture_file}")
    return tcpdump_output


def scp(  # pylint: disable=too-many-arguments
    console: _LinuxConsole,
    host: str,
    port: Union[int, str],
    username: str,
    password: str,
    src_path: str,
    dst_path: str,
    action: str = "download",
    timeout: int = 30,
) -> None:
    """SCP file.

    :param console: linux device or console instance
    :type console: LinuxConsole
    :param host: remote ssh host ip address
    :type host: str
    :param port: remove ssh host port number
    :type port: Union[int, str]
    :param username: ssh username
    :type username: str
    :param password: ssh password
    :type password: str
    :param src_path: source file path
    :type src_path: str
    :param dst_path: destination path
    :type dst_path: str
    :param action: scp action(download/upload)
    :type action: str
    :param timeout: scp timeout in seconds
    :type timeout: int
    :raises SCPConnectionError: on failed to scp file
    """
    if action == "download":
        command = (
            "scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
            f" -q -P {port} {username}@{host}:{src_path} {dst_path}"
        )
    else:
        command = (
            "scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
            f" -q -P {port} {src_path} {username}@{host}:{dst_path}"
        )
    console.sendline(command)
    if console.expect([pexpect.TIMEOUT, "assword:"], timeout=10):
        console.sendline(password)
    if console.expect_exact(["100%", pexpect.TIMEOUT], timeout=timeout):
        raise SCPConnectionError(f"Failed to scp from {src_path} to {dst_path}")


def traceroute_host(
    console: _LinuxConsole, host_ip: str, version: str = "", options: str = ""
) -> str:
    """Traceroute given host ip and return the details.

    :param console: linux device or console instance
    :type console: LinuxConsole
    :param host_ip: host ip address
    :type host_ip: str
    :param version: ip version
    :type version: str
    :param options: additional command line options
    :type options: str
    :return: traceroute command output
    :rtype: str
    """
    return console.execute_command(
        f"traceroute{version} {options} {host_ip}", timeout=90
    )


class IptablesFirewall:
    """Linux iptables firewall."""

    def __init__(self, console: _LinuxConsole) -> None:
        """Initialize IptablesFirewall.

        :param console: linux console or device instance
        :type console: LinuxConsole
        """
        self._console = console

    def get_iptables_list(
        self, opts: str = "", extra_opts: str = ""
    ) -> dict[str, list[dict]]:
        """Return iptables rules as dictionary.

        :param opts: command line arguments for iptables command
        :type opts: str
        :param extra_opts: extra command line arguments for iptables command
        :type extra_opts: str
        :return: iptables rules dictionary
        :rtype: Dict[str, List[Dict]]
        """
        return IptablesParser().ip6tables(
            self._console.execute_command(f"iptables {opts} {extra_opts}")
        )

    def is_iptable_empty(self, opts: str = "", extra_opts: str = "") -> bool:
        """Return True if iptables is empty.

        :param opts: command line arguments for iptables command
        :type opts: str
        :param extra_opts: extra command line arguments for iptables command
        :type extra_opts: str
        :return: True if iptables is empty, False otherwise
        :rtype: bool
        """
        return any(self.get_iptables_list(opts, extra_opts).values())

    def get_ip6tables_list(
        self, opts: str = "", extra_opts: str = ""
    ) -> dict[str, list[dict]]:
        """Return ip6tables rules as dictionary.

        :param opts: command line arguments for ip6tables command
        :type opts: str
        :param extra_opts: extra command line arguments for ip6tables command
        :type extra_opts: str
        :return: ip6tables rules dictionary
        :rtype: Dict[str, List[Dict]]
        """
        return IptablesParser().ip6tables(
            self._console.execute_command(f"ip6tables {opts} {extra_opts}")
        )

    def is_ip6table_empty(self, opts: str = "", extra_opts: str = "") -> bool:
        """Return True if ip6tables is empty.

        :param opts: command line arguments for ip6tables command
        :type opts: str
        :param extra_opts: extra command line arguments for ip6tables command
        :type extra_opts: str
        :return: True if ip6tables is empty, False otherwise
        :rtype: bool
        """
        return any(self.get_ip6tables_list(opts, extra_opts).values())

    def add_drop_rule_iptables(self, option: str, valid_ip: str) -> None:
        """Add drop rule to iptables.

        :param option: iptables command line options
        :type option: str
        :param valid_ip: ip to be blocked from device
        :type valid_ip: str
        :raises ValueError: on given iptables rule can't be added
        """
        iptables_output = self._console.execute_command(
            f"iptables -C INPUT {option} {valid_ip} -j DROP"
        )
        if re.search(rf"host\/network.*{valid_ip}.*not found", iptables_output):
            raise ValueError(
                f"Firewall rule cannot be added as the ip address: {valid_ip} could not"
                " be found"
            )
        if "Bad rule" in iptables_output:
            self._console.execute_command(
                f"iptables -I INPUT 1 {option} {valid_ip} -j DROP"
            )

    def add_drop_rule_ip6tables(self, option: str, valid_ip: str) -> None:
        """Add drop rule to ip6tables.

        :param option: ip6tables command line options
        :type option: str
        :param valid_ip: ip to be blocked from device
        :type valid_ip: str
        :raises ValueError: on given ip6tables rule can't be added
        """
        ip6tables_output = self._console.execute_command(
            f"ip6tables -C INPUT {option} {valid_ip} -j DROP"
        )
        if re.search(rf"host\/network.*{valid_ip}.*not found", ip6tables_output):
            raise ValueError(
                f"Firewall rule cannot be added as the ip address: {valid_ip} could not"
                " be found"
            )
        if "Bad rule" in ip6tables_output:
            self._console.execute_command(
                f"ip6tables -I INPUT 1 {option} {valid_ip} -j DROP"
            )

    def del_drop_rule_iptables(self, option: str, valid_ip: str) -> None:
        """Delete drop rule from iptables.

        :param option: iptables command line options
        :type option: str
        :param valid_ip: ip to be unblocked
        :type valid_ip: str
        """
        self._console.execute_command(f"iptables -D INPUT {option} {valid_ip} -j DROP")

    def del_drop_rule_ip6tables(self, option: str, valid_ip: str) -> None:
        """Delete drop rule from ip6tables.

        :param option: ip6tables command line options
        :type option: str
        :param valid_ip: ip to be unblocked
        :type valid_ip: str
        """
        self._console.execute_command(f"ip6tables -D INPUT {option} {valid_ip} -j DROP")


class NSLookup:
    """NSLookup command line utility."""

    def __init__(self, console: _LinuxConsole) -> None:
        """Initialize NSLookup.

        :param console: console or device instance
        :type console: LinuxConsole
        """
        self._hw = console

    def __call__(
        self, domain_name: str, opts: str = "", extra_opts: str = ""
    ) -> dict[str, Any]:
        """Run nslookup with given arguments and return the parsed results.

        :param domain_name: domain name to perform nslookup on
        :type domain_name: str
        :param opts: nslookup command line options
        :type opts: str
        :param extra_opts: nslookup additional command line options
        :type extra_opts: str
        :return: parsed nslookup results as dictionary
        :rtype: Dict[str, Any]
        """
        return self.nslookup(domain_name, opts, extra_opts)

    def nslookup(
        self, domain_name: str, opts: str = "", extra_opts: str = ""
    ) -> dict[str, Any]:
        """Run nslookup with given arguments and return the parsed results.

        :param domain_name: domain name to perform nslookup on
        :type domain_name: str
        :param opts: nslookup command line options
        :type opts: str
        :param extra_opts: nslookup additional command line options
        :type extra_opts: str
        :return: parsed nslookup results as dictionary
        :rtype: Dict[str, Any]
        """
        return NslookupParser().parse_nslookup_output(
            self._hw.execute_command(f"nslookup {opts} {domain_name} {extra_opts}")
        )


# pylint: disable=too-few-public-methods,too-many-instance-attributes
class DNS:
    """Holds DNS names and their addresses."""

    _dns_name_suffix = ".boardfarm.com"

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        console: _LinuxConsole,
        device_name: str,
        ipv4_address: str = None,
        ipv6_address: str = None,
        ipv4_aux_address: IPv4Address = None,
        ipv6_aux_address: IPv6Address = None,
        aux_url: str = None,
    ) -> None:
        """Initialize DNS.

        :param console: console or device instance
        :type console: LinuxConsole
        :param device_name: device name
        :type device_name: str
        :param ipv4_address: ipv4 address of the device
        :type ipv4_address: str
        :param ipv6_address: ipv6 address of the device
        :type ipv6_address: str
        :param ipv4_aux_address: ipv4 aux address
        :type ipv4_aux_address: IPv4Address
        :param ipv6_aux_address: ipv6 aux address
        :type ipv6_aux_address: IPv6Address
        :param aux_url: aux url
        :type aux_url: str
        """
        self.auxv4 = ipv4_aux_address
        self.auxv6 = ipv6_aux_address
        self._device_name = device_name
        self.dnsv4: DefaultDict = defaultdict(list)
        self.dnsv6: DefaultDict = defaultdict(list)
        self.hosts_v4: DefaultDict = defaultdict(list)
        self.hosts_v6: DefaultDict = defaultdict(list)
        self._add_dns_addresses(ipv4_address, ipv6_address)
        self._add_aux_dns_addresses(aux_url)
        self.hosts_v4.update(self.dnsv4)
        self.hosts_v6.update(self.dnsv6)
        self.nslookup = NSLookup(console)

    def _add_dns_addresses(
        self, ipv4_address: Optional[str], ipv6_address: Optional[str]
    ) -> None:
        if ipv4_address is not None:
            self.dnsv4[f"{self._device_name}{self._dns_name_suffix}"].append(
                ipv4_address
            )
        if ipv6_address is not None:
            self.dnsv6[f"{self._device_name}{self._dns_name_suffix}"].append(
                ipv6_address
            )

    def _add_aux_dns_addresses(self, aux_url: Optional[str]) -> None:
        if self.auxv4 is not None:
            self.dnsv4[f"{self._device_name}{self._dns_name_suffix}"].append(self.auxv4)
            if aux_url is not None:
                self.dnsv4[aux_url].append(self.auxv4)
        if self.auxv6 is not None:
            self.dnsv6[f"{self._device_name}{self._dns_name_suffix}"].append(self.auxv6)
            if aux_url is not None:
                self.dnsv6[aux_url].append(self.auxv6)

    def configure_hosts(
        self,
        reachable_ipv4: int,
        unreachable_ipv4: int,
        reachable_ipv6: int,
        unreachable_ipv6: int,
    ) -> None:
        """Create the given number of reachable and unreachable ACS domain IP's.

        :param reachable_ipv4: no.of reachable IPv4 address for acs url
        :type reachable_ipv4: int
        :param unreachable_ipv4: no.of unreachable IPv4 address for acs url
        :type unreachable_ipv4: int
        :param reachable_ipv6: no.of reachable IPv6 address for acs url
        :type reachable_ipv6: int
        :param unreachable_ipv6: no.of unreachable IPv6 address for acs url
        :type unreachable_ipv6: int
        """
        val_v4 = self.hosts_v4[f"{self._device_name}{self._dns_name_suffix}"][
            :reachable_ipv4
        ]
        val_v6 = self.hosts_v6[f"{self._device_name}{self._dns_name_suffix}"][
            :reachable_ipv6
        ]
        self.hosts_v4[f"{self._device_name}{self._dns_name_suffix}"] = val_v4
        self.hosts_v6[f"{self._device_name}{self._dns_name_suffix}"] = val_v6
        for val in range(unreachable_ipv4):
            ipv4 = self.auxv4 + (val + 1)
            self.hosts_v4[f"{self._device_name}{self._dns_name_suffix}"].append(
                str(ipv4)
            )
        for val in range(unreachable_ipv6):
            ipv6 = self.auxv6 + (val + 1)
            self.hosts_v6[f"{self._device_name}{self._dns_name_suffix}"].append(
                str(ipv6)
            )


class HTTPResult:  # pylint: disable=too-few-public-methods
    """Class to save the object of parsed HTTP response."""

    def __init__(self, response: str) -> None:
        """Parse the response and save it as an instance.

        :param response: response from HTTP request
        :type response: str
        :raises UseCaseFailure: in case the response has some error
        """
        self.response = response
        self.raw, self.code, self.beautified_text = self._parse_response(response)

    @staticmethod
    def _parse_response(response: str) -> tuple[str, str, str]:
        if "Connection refused" in response or "Connection timed out" in response:
            raise UseCaseFailure(f"Curl Failure due to the following reason {response}")
        raw = re.findall(r"\<(\!DOC|head).*\>", response, re.S)[0]
        code = re.findall(r"< HTTP\/.*\s(\d+)", response)[0]
        beautified_text = BeautifulSoup(raw, "html.parser").prettify()
        return raw, code, beautified_text


def http_get(console: _LinuxConsole, url: str, timeout: int = 20) -> HTTPResult:
    """Peform http get (via curl) and return parsed result.

    :param console: console or device instance
    :type console: _LinuxConsole
    :param url: url to get the response
    :type url: str
    :param timeout: connection timeout for the curl command in seconds
    :type timeout: int
    :return: parsed http response
    :rtype: HTTPResult
    """
    return HTTPResult(
        console.execute_command(f"curl -v --connect-timeout {timeout} {url}")
    )


def dns_lookup(console: _LinuxConsole, domain_name: str) -> list[dict[str, Any]]:
    """Perform ``dig`` command in the devices to resolve DNS.

    :param console: console or device instance
    :type console: _LinuxConsole
    :param domain_name: domain name which needs lookup
    :type domain_name: str
    :return: parsed dig command ouput
    :rtype: List[Dict[str, Any]]
    """
    return jc.parsers.dig.parse(
        console.execute_command(f"dig {domain_name}").split(";", 1)[-1]
    )