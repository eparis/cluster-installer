#!/usr/bin/env python

import argparse
import getpass
import multiprocessing.dummy
import yaml
from install import SingleInstaller

user = getpass.getuser()

DEFAULT_VERSION = "v4.3.0"

per_cloud = {
        'aws': 0,
        'gcp': 0,
        'azure': 0,
}
create_keys = ["name", "version", "cloud", "master-size", "worker-size", "profile"]
destroy_keys = ["name", "versions"]

def set_cluster_defaults(cluster):
    cloud = cluster["cloud"]
    if "name" not in cluster:
        cluster["name"] = "summit-%s%d" % (cloud, per_cloud[cloud])
        per_cloud[cloud] += 1
    if "version" not in cluster:
        cluster["version"] = DEFAULT_VERSION
    if "master-size" not in cluster:
        cluster["master-size"] = "default"
    if "worker-size" not in cluster:
        cluster["worker-size"] = "default"
    if "profile" not in cluster:
        cluster["profile"] = "default"
    return cluster

def get_all_clusters():
    with open("clusters.yaml") as f:
        clusters = yaml.safe_load(f)
    clusters = clusters["clusters"]
    for i, cluster in enumerate(clusters):
        cluster = set_cluster_defaults(cluster)
        clusters[i] = cluster
    return clusters

def get_cluster_arg(cluster, action):
    cluster_arg = []
    cluster_arg.append(action)
    for key in cluster:
        if action == "create" and key not in create_keys:
            continue
        if action == "destroy" and key not in destroy_keys:
            continue
        value = cluster[key]
        if key == "name":
            value = "%s-%s" % (user, value)
        cluster_arg.append("--%s=%s" % (key, value))
    return cluster_arg


actions = {
    'create': 'create',
    'destroy': 'destroy',
}
def get_args(action):
    action = actions[action]

    cluster_args = []
    for cluster in get_all_clusters():
        args = get_cluster_arg(cluster, action)
        cluster_args.append(args)
    return cluster_args

def get_create_args():
    return get_args("create")

def create_clusters():
    create_args = get_create_args()
    do_args(create_args)

def get_destroy_args():
    return get_args("destroy")

def destroy_clusters():
    destroy_args = get_destroy_args()
    do_args(destroy_args)

def do_arg(cluster_arg):
    single_installer = SingleInstaller()
    parser = single_installer.parser()
    print(cluster_arg)
    args = parser.parse_args(cluster_arg)
    print("Handling %s" % args.name)
    args.func(args)
    print(args.stdout)

def do_args(cluster_args):
    # do up to 8 at a time.
    with multiprocessing.dummy.Pool(processes=8) as pool:
        pool.map(do_arg, cluster_args)

def get_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='command', required=True)

    parser_create = subparsers.add_parser('create', help='Install all cluster')
    parser_create.set_defaults(func=create_clusters)

    parser_destroy = subparsers.add_parser('destroy', help='Destroy all cluster')
    parser_destroy.set_defaults(func=destroy_clusters)
    return parser

def main():
    parser = get_parser()
    args = parser.parse_args()
    args.func()

if __name__ == "__main__":
    main()
