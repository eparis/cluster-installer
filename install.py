#!/usr/bin/env python

import argparse
import configparser
import copy
import getpass
import glob
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import urllib.request
import uuid
import yaml
from pick import pick

NODE_NONE = {}

INSTALL_CONFIG = """apiVersion: v1
baseDomain: BASEDOMAIN
compute:
- hyperthreading: Enabled
  name: worker
  platform: {}
  replicas: 3
controlPlane:
  hyperthreading: Enabled
  name: master
  platform: {}
  replicas: 3
metadata:
  name: CLUSTERNAME
networking:
  clusterNetwork:
  - cidr: 10.128.0.0/14
    hostPrefix: 23
  machineCIDR: 10.0.0.0/16
  networkType: OpenShiftSDN
  serviceNetwork:
  - 172.30.0.0/16
platform: PLATFORM
publish: External
pullSecret: PULLSECRET
sshKey: SSHKEY"""

def get_cloud_info(args):
    class CloudData:
        def __init__(self, platform):
            self._install_config = yaml.safe_load(INSTALL_CONFIG)
            self._install_config['platform'] = platform
            self.envs = os.environ.copy()

            path = "./pullsecret"
            self._set_pull_secret(path)

            path = str(Path.home()) + "/.ssh/openshift-dev.pub"
            self._set_ssh_key(path)

        def set_cluster_name(self, name):
            self._install_config['metadata']['name'] = name

        def _set_pull_secret(self, path):
            with open(path, 'r') as f:
                pullsecret = f.read()
                pullsecret = pullsecret.replace('\n', '').replace(' ', '')
                self._install_config['pullSecret'] = pullsecret

        def _set_ssh_key(self, path):
            with open(path, 'r') as f:
                ssh_key = f.read()
                ssh_key = ssh_key.strip()
                self._install_config['sshKey'] = ssh_key

        def install_config(self):
            return yaml.dump(self._install_config)

        def write_install_config(self, dir):
            os.mkdir(dir)
            path = os.path.join(dir, 'install-config.yaml')
            with open(path, 'w') as f:
                yaml.dump(self._install_config, f)


    class AWSData(CloudData):
        def get_credentials(self):
            path = os.path.join(Path.home(), ".aws/credentials")
            creds = configparser.ConfigParser()
            creds.read(path)
            return creds.sections()

        AWS_BASE_DOMAINS = {
            'default': 'devcluster.openshift.com',
            'long-lived': 'llc.devcluster.openshift.com',
            'openshift-dev': 'devcluster.openshift.com',
        }

        AWS_SMALL = "m5.large"
        AWS_MEDIUM = "m5.2xlarge"
        AWS_LARGE = "m5.8xlarge"
        def _instance_dict(instance):
            return {
                'aws': {
                    'type': instance
                }
            }
        AWS_INSTANCES = {
            'default': {},
            'small': _instance_dict(AWS_SMALL),
            'medium': _instance_dict(AWS_MEDIUM),
            'large': _instance_dict(AWS_LARGE),
        }

        AWS_PLATFORM = {
            'aws': {
                'region': 'us-east-2',
            }
        }

        def __init__(self, args):
            CloudData.__init__(self, platform=self.AWS_PLATFORM)

            profile_name = args.profile
            if not profile_name:
                profile_name, _ = pick(self.get_credentials())
            self.envs['AWS_PROFILE'] = profile_name
            if profile_name in self.AWS_BASE_DOMAINS:
                self.base_domain = self.AWS_BASE_DOMAINS[profile_name]
            else:
                raise KeyError("don't konw base domain for %s" % profile_name)
            self._install_config['baseDomain'] = self.base_domain

            instance = args.master_size
            if not instance:
                instance, _ = pick(list(self.AWS_INSTANCES.keys()), 'Pick Master Instance')
            self._install_config['controlPlane']['platform'] = copy.deepcopy(self.AWS_INSTANCES[instance])

            instance = args.worker_size
            if not instance:
                instance, _ = pick(list(self.AWS_INSTANCES.keys()), 'Pick Worker Instance')
            self._install_config['compute'][0]['platform'] = copy.deepcopy(self.AWS_INSTANCES[instance])

    class GCPData(CloudData):
        GCP_PLATFORM = {
            'gcp': {
                'projectID': 'openshift-gce-devel',
                'region': 'us-central1',
            }
        }
        # GCP_DEFAULT_MASTER = 'n1-standard-4'
        # GCP_DEFAUKT_WORKER = 'n1-standard-4'
        GCP_PROFILES = {
            'openshift-gce-devel': {'base_domain': 'gcp.devcluster.openshift.com'},
        }
        def __init__(self, args):
            CloudData.__init__(self, platform=self.GCP_PLATFORM)
            profile_name = args.profile
            if not profile_name:
                profile_name, _ = pick(list(self.GCP_PROFILES.keys()), 'Pick Profile')
            profile = self.GCP_PROFILES[profile_name]
            self._install_config['baseDomain'] = profile['base_domain']

    class AzureData(CloudData):
        AZURE_PLATFORM = {
            'azure': {
                'baseDomainResourceGroupName': 'os4-common',
                'region': 'centralus',
            }
        }
        AZURE_BASE_DOMAINS = {
            'OpenShift Architects': 'architects.azure.devcluster.openshift.com',
        }
        def __init__(self, args):
            CloudData.__init__(self, platform=self.AZURE_PLATFORM)
            subscription = args.profile
            if not subscription:
                subscription, _ = pick(list(self.AZURE_BASE_DOMAINS.keys()), 'Pick Subscription')
            self._install_config['baseDomain'] = self.AZURE_BASE_DOMAINS[subscription]

    # This is the start of the actual code for get_cloud_info()
    CLOUDS = {
        'aws': AWSData,
        'azure': AzureData,
        'gcp': GCPData,
    }
    cloud = args.cloud
    if not cloud:
        cloud, _ = pick(list(CLOUDS.keys()), 'Pick Cloud')
    cloud_data = CLOUDS[cloud](args=args)
    return cloud, cloud_data

def generate_cluster_name(cloud, args):
    if args.name:
        return args.name
    uid = str(uuid.uuid4())
    uid = uid[:8]
    user = getpass.getuser()
    name = "%s-%s-%s" % (user, cloud, uid)
    return name

class Versions:
    def __init__(self, args, latest_cached=False):
        self.bin_cache_dir()
        self.install_path = self.get_version(args, latest_cached)

    def bin_cache_dir(self):
        cwd = os.getcwd()
        self.bdir = os.path.join(cwd, ".bins")
        os.makedirs(self.bdir, exist_ok=True)

    def download_version(self, version, url):
        if url.startswith("/"):
            return url
        print("Downloading %s" % url)
        file_name = os.path.join(self.bdir, "openshift-install-"+version)
        file_tmp = urllib.request.urlretrieve(url, filename=None)[0]
        with tarfile.open(file_tmp) as tar:
            openshift_install = tar.extractfile("openshift-install")
            with open(file_name, 'wb') as out_file:
                out_file.write(openshift_install.read())
        os.chmod(file_name, stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH | stat.S_IXOTH)
        return file_name

    def get_latest_nightly_versions(self):
        versions = {}
        for i in range(3, 5):
            major = "4.%d" % i
            latest_release = "https://mirror.openshift.com/pub/openshift-v4/clients/ocp-dev-preview/latest-%s/release.txt" % major
            with urllib.request.urlopen(latest_release) as response:
                for line in response:
                    line = line.decode('utf-8')  # Decoding the binary data to text.
                    if not line.startswith("Name"):
                        continue
                    version = line.split()[-1]
                    url = 'https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest-%s/openshift-install-linux-%s.tar.gz' % (major, version)
                    versions["nightly-"+version] = {
                        'url': url,
                        'cached': False,
                    }
                    break
        return versions

    def get_latest_release_versions(self):
        versions = {}
        for i in range(1, 4):
            major = "4.%d" % i
            latest_release = "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest-%s/release.txt" % major
            with urllib.request.urlopen(latest_release) as response:
                for line in response:
                    line = line.decode('utf-8')  # Decoding the binary data to text.
                    if not line.startswith("Name"):
                        continue
                    version = line.split()[-1]
                    url = 'https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest-%s/openshift-install-linux-%s.tar.gz' % (major, version)
                    versions["v"+version] = {
                        'url': url,
                        'cached': False,
                    }
                    break
        return versions

    def get_cached_versions(self):
        versions = {}
        globthing = os.path.join(self.bdir, "openshift-install-*")
        files = glob.glob(globthing)
        for f in files:
            version = f.split("/")[-1]
            version = version[len("openshift-install-"):]
            versions[version] = {
                'url': f,
                'cached': True,
            }
        return versions

    def which(self, program):
        def is_exe(fpath):
            return os.path.isfile(fpath) and os.access(fpath, os.X_OK)
        fpath, fname = os.path.split(program)
        if fpath:
            if is_exe(program):
                return program
        else:
            for path in os.environ["PATH"].split(os.pathsep):
                exe_file = os.path.join(path, program)
                if is_exe(exe_file):
                    return exe_file
        return None

    def get_path_version(self):
        path = self.which("openshift-install")
        ret = subprocess.run([path, "version"], check=True, capture_output=True, text=True)
        version = ret.stdout.split(sep='\n')[0].split()[-1]
        return {
            version: {
                'url': path,
                'cached': True,
            }
        }

    def list_versions(self):
        versions = {}
        print("Downloading list of versions.")
        versions.update(self.get_latest_nightly_versions())
        versions.update(self.get_latest_release_versions())
        versions.update(self.get_cached_versions())
        versions.update(self.get_path_version())
        return versions

    def cached_versions(self, versions):
        out = {}
        for version in versions:
            if not versions[version]['cached']:
                continue
            out[version] = versions[version]
        return out

    def latest_version(self, version_dict):
        versions = version_dict.keys()
        versions = list(versions)
        versions.sort()
        return versions[-1]

    def get_version(self, args, latest_cached):
        versions = self.list_versions()
        version = args.version
        if not version and latest_cached:
            version = self.latest_version(self.cached_versions(versions))
        if not version:
            version, _ = pick(list(versions.keys()), "Pick a versions")
        url = versions[version]['url']
        path = self.download_version(version, url)
        return path

    def install(self, path, env):
        cwd = os.getcwd()
        os.chdir(path)
        subprocess.run([self.install_path, "create", "cluster"], check=True, text=True, env=env)
        os.chdir(cwd)

    def destroy(self, path):
        cwd = os.getcwd()
        os.chdir(path)
        subprocess.run([self.install_path, "destroy", "cluster"], check=True, text=True)
        os.chdir(cwd)

def get_cluster_dir(args):
    cloud, cloud_data = get_cloud_info(args=args)
    name = generate_cluster_name(cloud, args)
    path = os.path.join(os.getcwd(), name)
    print("Cluster Name: %s" % name)
    cloud_data.set_cluster_name(name)
    cloud_data.write_install_config(path)
    return path, cloud_data.envs

def install_cluster(args):
    cluster_dir, env = get_cluster_dir(args=args)
    version = Versions(args=args)
    version.install(path=cluster_dir, env=env)

def get_running_clusters():
    dirs = glob.glob("*/metadata.json")
    dirs = [cluster.split("/")[0] for cluster in dirs]
    return dirs

def cluster_to_destroy(args):
    cluster = args.name
    if not cluster:
        cluster, _ = pick(get_running_clusters(), 'Cluster To Destroy')
    return cluster

def destroy_cluster(args):
    cluster = cluster_to_destroy(args)
    version = Versions(args=args, latest_cached=True)
    print("Destroying %s" % cluster)
    version.destroy(cluster)
    shutil.rmtree(cluster)

class SingleInstaller():
    def parser(self):
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest='command', required=True)

        global_parser = argparse.ArgumentParser(add_help=False)q
        global_parser.add_argument('--version')
        global_parser.add_argument('--name')

        # create the parser for the "a" command
        parser_create = subparsers.add_parser('create', help='Install a cluster', parents=[global_parser])
        parser_create.set_defaults(func=install_cluster)
        parser_create.add_argument('--cloud')
        parser_create.add_argument('--profile')
        parser_create.add_argument('--master-size')
        parser_create.add_argument('--worker-size')

        parser_destroy = subparsers.add_parser('destroy', help='Destroy a cluster', parents=[global_parser])
        parser_destroy.set_defaults(func=destroy_cluster)
        return parser

    def main(self):
        parser = self.parser()
        args = parser.parse_args()
        args.func(args)

if __name__ == "__main__":
    single_installer = SingleInstaller()
    single_installer.main()
