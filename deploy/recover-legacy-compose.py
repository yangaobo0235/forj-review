"""Recover one review container's runtime definition; stdin/stdout contain secrets.

Run only through start-review.sh, with stdout redirected to a mode-0600 file.
Use actual Docker runtime values, not a guessed historical Compose template.
"""

import json
import sys


def recover(container):
    if container.get("Name") != "/review-agent-service":
        raise ValueError("Unexpected container name")
    config, host = container["Config"], container["HostConfig"]
    # Fail explicitly for features this recovery path cannot represent faithfully.
    for key in ("Devices", "DeviceRequests", "DeviceCgroupRules", "VolumesFrom", "Links",
                "CpuRealtimePeriod", "CpuRealtimeRuntime", "CpusetMems"):
        if host.get(key):
            raise ValueError("Unsupported legacy container setting: " + key)
    if config.get("MacAddress"):
        raise ValueError("Unsupported legacy MAC address override")
    for mount in host.get("Mounts") or []:
        if mount.get("VolumeOptions") or (mount.get("BindOptions") or {}).get("NonRecursive"):
            raise ValueError("Unsupported legacy mount options")

    service = {"image": container["Image"], "container_name": "review-agent-service"}
    for source, target in (("Env", "environment"), ("Entrypoint", "entrypoint"),
                           ("Cmd", "command"), ("User", "user"), ("WorkingDir", "working_dir"),
                           ("Hostname", "hostname"), ("Domainname", "domainname"),
                           ("StopSignal", "stop_signal")):
        if config.get(source) is not None:
            service[target] = config[source]
    for source, target in (("Tty", "tty"), ("OpenStdin", "stdin_open")):
        if config.get(source):
            service[target] = True
    if config.get("StopTimeout") is not None:
        service["stop_grace_period"] = str(config["StopTimeout"]) + "s"
    labels = {key: value for key, value in (config.get("Labels") or {}).items()
              if not key.startswith("com.docker.compose.")}
    if labels:
        service["labels"] = labels

    for source, target in (
        ("ReadonlyRootfs", "read_only"), ("Privileged", "privileged"), ("Init", "init"),
        ("CapAdd", "cap_add"), ("CapDrop", "cap_drop"), ("SecurityOpt", "security_opt"),
        ("Dns", "dns"), ("DnsOptions", "dns_opt"), ("DnsSearch", "dns_search"),
        ("ExtraHosts", "extra_hosts"), ("Sysctls", "sysctls"), ("GroupAdd", "group_add"),
        ("Memory", "mem_limit"), ("MemoryReservation", "mem_reservation"),
        ("MemorySwap", "memswap_limit"), ("MemorySwappiness", "mem_swappiness"),
        ("OomKillDisable", "oom_kill_disable"), ("OomScoreAdj", "oom_score_adj"),
        ("CpuShares", "cpu_shares"), ("CpuPeriod", "cpu_period"), ("CpuQuota", "cpu_quota"),
        ("CpusetCpus", "cpuset"), ("ShmSize", "shm_size"), ("PidsLimit", "pids_limit"),
        ("PidMode", "pid"), ("IpcMode", "ipc"), ("UTSMode", "uts"),
        ("UsernsMode", "userns_mode"), ("CgroupParent", "cgroup_parent"),
        ("CgroupnsMode", "cgroup"),
    ):
        if host.get(source):
            service[target] = host[source]
    if host.get("NanoCpus"):
        service["cpus"] = host["NanoCpus"] / 1_000_000_000
    if host.get("Ulimits"):
        service["ulimits"] = {limit["Name"]: {"soft": limit["Soft"], "hard": limit["Hard"]}
                              for limit in host["Ulimits"]}
    restart = host.get("RestartPolicy") or {}
    if restart.get("Name"):
        service["restart"] = restart["Name"]
        if restart["Name"] == "on-failure" and restart.get("MaximumRetryCount"):
            service["restart"] += ":" + str(restart["MaximumRetryCount"])
    log = host.get("LogConfig") or {}
    if log.get("Type"):
        service["logging"] = {"driver": log["Type"], "options": log.get("Config") or {}}
    health = config.get("Healthcheck") or {}
    if health:
        check = {"test": health["Test"]}
        for source, target in (("Interval", "interval"), ("Timeout", "timeout"),
                               ("StartPeriod", "start_period"), ("StartInterval", "start_interval")):
            if health.get(source):
                check[target] = str(health[source]) + "ns"
        if health.get("Retries"):
            check["retries"] = health["Retries"]
        service["healthcheck"] = check

    ports = []
    for exposed, bindings in (host.get("PortBindings") or {}).items():
        target, protocol = exposed.split("/", 1)
        for binding in bindings or []:
            if not binding.get("HostPort"):
                raise ValueError("Cannot recover a dynamically assigned legacy port")
            port = {"target": int(target), "published": str(binding["HostPort"]), "protocol": protocol}
            if binding.get("HostIp"):
                port["host_ip"] = binding["HostIp"]
            ports.append(port)
    if ports:
        service["ports"] = ports

    document = {"services": {"review-agent": service}}
    volumes = []
    for index, mount in enumerate(container.get("Mounts") or []):
        kind = mount["Type"]
        if kind == "tmpfs":
            continue
        if kind not in {"bind", "volume"}:
            raise ValueError("Unsupported legacy mount type")
        entry = {"type": kind, "target": mount["Destination"], "read_only": not mount["RW"]}
        if kind == "volume":
            name = "legacy-volume-" + str(index)
            document.setdefault("volumes", {})[name] = {"external": True, "name": mount["Name"]}
            entry["source"] = name
        else:
            entry["source"] = mount["Source"]
            if mount.get("Propagation"):
                entry["bind"] = {"propagation": mount["Propagation"]}
        volumes.append(entry)
    if volumes:
        service["volumes"] = volumes
    if host.get("Tmpfs"):
        service["tmpfs"] = [path + (":" + options if options else "") for path, options in host["Tmpfs"].items()]

    mode = host.get("NetworkMode") or "default"
    if mode in {"host", "none", "bridge"} or mode.startswith("container:"):
        service["network_mode"] = mode
    else:
        networks = container.get("NetworkSettings", {}).get("Networks") or {}
        if not networks:
            raise ValueError("Cannot recover legacy network configuration")
        for index, (name, network) in enumerate(networks.items()):
            key = "legacy-network-" + str(index)
            document.setdefault("networks", {})[key] = {"external": True, "name": name}
            options = {}
            if network.get("Aliases"):
                options["aliases"] = network["Aliases"]
            ipam = network.get("IPAMConfig") or {}
            for source, target in (("IPv4Address", "ipv4_address"), ("IPv6Address", "ipv6_address"),
                                   ("LinkLocalIPs", "link_local_ips")):
                if ipam.get(source):
                    options[target] = ipam[source]
            service.setdefault("networks", {})[key] = options
    return document


def escape_interpolation(value):
    """Compose interpolates strings even in JSON; preserve literal runtime dollars."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [escape_interpolation(item) for item in value]
    if isinstance(value, dict):
        return {key: escape_interpolation(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    try:
        containers = json.load(sys.stdin)
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError("Expected exactly one review container")
        json.dump(escape_interpolation(recover(containers[0])), sys.stdout, indent=2)
        sys.stdout.write("\n")
    except (KeyError, TypeError, ValueError):
        # Do not print exception values: input can contain credentials.
        sys.stderr.write("Cannot faithfully recover the legacy review container; no runtime files replaced.\n")
        sys.exit(1)
