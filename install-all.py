#!/usr/bin/env python

import getpass
from install import SingleInstaller

user = getpass.getuser()

clusters = [
    #["--cloud=aws", "--name=%s-summit-aws1" % user, "--master-size=default", "--worker-size=large", "--profile=default"],
    ["--cloud=gcp", "--name=%s-summit-gcp1" % user, "--master-size=default", "--worker-size=default", "--profile=default"],
    ["--cloud=azure", "--name=%s-summit-azure1" % user, "--master-size=default", "--worker-size=default", "--profile=default"],
]

single_installer = SingleInstaller()
parser = single_installer.parser()
for cluster in clusters:
    cmd = ["create", "--version=v4.3.0"] + cluster
    args = parser.parse_args(cmd)
    print("Creating %s" % args.name)
    args.func(args)
