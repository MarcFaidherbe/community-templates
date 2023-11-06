#!/usr/bin/env python3
#####################################################################################
# zypper-updateinfo.py - Retrieves available security and package updates using 
#                        Zypper and send it to Zabbix.
#
# Author: robin.roevens (at) disroot.org
# Version: 1.0.1
#
# Requires: python >= 3.4
#           zabbix-sender
#
# Host in Zabbix should be configured with the "Template Module Zypper updateinfo by 
# Zabbix trapper" template.
# Schedule this script to run each hour or so using a Systemd timer or cron job.

import os
import sys
import subprocess
import xml.etree.ElementTree as ET
import json
import tempfile
import socket

## Configuration
# zabbix_sender binary:
zabbix_sender_bin = "/usr/bin/zabbix_sender"
# zabbix agent config path and filename:
zabbix_agent_config = "/etc/zabbix/zabbix_agent2.conf"
# host on zabbix to send items to. Use '-' to send to the host defined in the agent config (Hostname):
host_hostname = socket.gethostname()

# Available categories/severities
categories = ["security", "recommended", "optional", "feature", "document", "yast"]
severities = ["critical", "important", "moderate", "low", "unspecified"]

# Item-key prefix for all items generated by this script
zabbix_item_key_prefix = "zypper.updateinfo"

def zypper_cmd(params):
    # Execute zypper with given params and return ElementTree with result
    command = f"/usr/bin/zypper -q --xmlout {params}"

    try:
        output = subprocess.check_output(command, shell=True)
    except subprocess.CalledProcessError as zypper_exc:
        print(f"Error getting patch list: {zypper_exc.returncode}\n{zypper_exc.output.decode('utf-8')}")
        exit(zypper_exc.returncode)
    
    return ET.fromstring(output.decode("utf-8"))

def zabbix_sender(hostname, trapitems, configfile):
    # Send a dict of Zabbix trapper items and their values to Zabbix server
    with tempfile.NamedTemporaryFile(buffering=0) as fp:
        for key, value in trapitems.items():
            line = "{hostname} {zabbix_item_key_prefix}.{key} {value}\n"\
                .format(hostname=hostname, zabbix_item_key_prefix=zabbix_item_key_prefix, key=key, value=value)
            fp.write(str.encode(line))

        command = "{zabbix_sender_bin} -c {configfile} -i \"{fp_name}\""\
            .format(zabbix_sender_bin=zabbix_sender_bin, configfile=configfile, fp_name=fp.name)
        try:
            output = subprocess.check_output(command, shell=True)
        except subprocess.CalledProcessError as zabbix_sender_exc:
            print("Error running {zabbix_sender_bin}: {zabbix_sender_exc_rc}\n{zabbix_sender_exc_output}"\
                .format(zabbix_sender_bin=zabbix_sender_bin, zabbix_sender_exc_rc=zabbix_sender_exc.returncode, \
                    zabbix_sender_exc_output=zabbix_sender_exc.output.decode('utf-8')))
        else: 
            print(output.decode("utf-8"))

def patch_category_discovery():
    # Generate patch categories discovery output
    patch_category_discovery = []
    for category in categories:
        for severity in severities:
            patch_category_discovery.append({
                "{#CATEGORY}": category,
                "{#SEVERITY}": severity})

    return json.dumps(patch_category_discovery)

def repositories_discovery(repolist):
    # Generate repository discovery output
    repo_discovery = []
    repositories = []
    for repository in repolist.findall("repo-list/repo"):
        repo_discovery.append({ 
            "{#ALIAS}": repository.get("alias"),
            "{#NAME}": repository.get("name"),
            "{#ENABLED}": repository.get("enabled"),
            "{#AUTOREFRESH}": repository.get("autorefresh")}
        )
        repositories.append(repository.get("alias"))

    return repositories, json.dumps(repo_discovery)

def main():
    discovery_items = {}
    update_info = {
        "patches": {},
        "packages": {}
    }

    if not (os.path.isfile(zabbix_sender_bin) or os.access(zabbix_sender_bin, os.X_OK)):
        sys.exit("Zabbix sender {zabbix_sender_bin} was not found or is not executable.".format(zabbix_sender_bin=zabbix_sender_bin))
    
    if not (os.path.isfile(zabbix_agent_config) or os.access(zabbix_agent_config, os.R_OK)):
        sys.exit("Zabbix config {zabbix_agent_config} was not found or is not readable.".format(zabbix_agent_config=zabbix_agent_config))

    discovery_items["patch_category.discovery"] = patch_category_discovery()

    print("Retrieving available repositories...")
    repositories, discovery_items["repositories.discovery"] = repositories_discovery(zypper_cmd('repos'))

    print("Sending discovery information to Zabbix...")
    zabbix_sender(host_hostname, discovery_items, zabbix_agent_config)

    print("Retrieving available patches...")
    patchlist = zypper_cmd("list-patches")

    print("Retrieving available package updates...")
    packagelist = zypper_cmd("list-updates")

    # Generate patch update items
    for category in categories:
        update_info["patches"][category] = {}
        for severity in severities:
            patch_count = len(patchlist.findall("update-status/*/update[@category='{category}'][@severity='{severity}']"\
                .format(category=category, severity=severity)))
            update_info["patches"][category][severity] = patch_count

    # Generate list of known vulnerabilitiess
    vulnerabilities = []
    for vulnerability in patchlist.findall("update-status/*/update/issue-list/issue[@type='cve']"):
        vulnerabilities.append(vulnerability.get('id'))
    # Remove duplicates and sort
    vulnerabilities = list(dict.fromkeys(vulnerabilities))
    vulnerabilities.sort()
    update_info["patches"]["security"]["cves"] = ", ".join(vulnerabilities)

    # Generate package update items
    package_updates = packagelist.findall("update-status/*/update[@kind='package']")
    package_count_total = len(package_updates)
    update_info["packages"]["all"] = package_count_total

    for repository in repositories:
        package_count = len(packagelist.findall("update-status/*/update[@kind='package']/source[@alias='{repository}']"\
            .format(repository=repository)))
        update_info["packages"][repository] = package_count

    # Generate package list
    packages = []
    for package in package_updates:
        packages.append("{package_name}.{package_arch}".format(package_name=package.get('name'), package_arch=package.get('arch')))
    update_info["packages"]["list"] = ", ".join(packages)

    # Send results to Zabbix
    print("Sending results to Zabbix...")
    zabbix_sender(host_hostname, {"raw": json.dumps(update_info)}, zabbix_agent_config)

if __name__ == "__main__":
    main()