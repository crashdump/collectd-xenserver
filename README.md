collectd-xenserver
==================

A Collectd plugin to monitor Citrix XenServer

------------------

This is a module for collectd. It try to fetch the last metrics from a Citrix Xenserver
host and the VMs running on it. This is done by fetching and parsing a xml on the server:

http://<username>:<password>@<host>/rrd_updates?start=<secondssinceepoch>&host=true

For more informations about this API, see the Citrix documentation here:

http://docs.vmd.citrix.com/XenServer/6.1.0/1.0/en_gb/sdk.html#persistent_perf_stats

Dependencies:
  - XenAPI python module: http://pypi.python.org/pypi/XenAPI
  - collectd python module: http://pypi.python.org/pypi/collectd

collectd.conf example:
```
  <Plugin python>
        ModulePath "/path/to/modules/"
        LogTraces true
        Interactive false
        Import "collectd-xenserver"
        <Module "collectd-xenserver">
              Host "10.0.0.100"
              User "root"
              Password "mysecretpassword"
        </Module>
  </Plugin>
```