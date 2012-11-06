#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This is a module for collectd. It try to fetch the last metrics from a Citrix Xenserver
host and the VMs running on it. This is done by fetching and parsing a xml on the server:

http://<username>:<password>@<host>/rrd_updates?start=<secondssinceepoch>&host=true

For more informations about this API, see the Citrix documentation here:

http://docs.vmd.citrix.com/XenServer/6.1.0/1.0/en_gb/sdk.html#persistent_perf_stats

Dependencies:
  - XenAPI python module: http://pypi.python.org/pypi/XenAPI
  - collectd python module: http://pypi.python.org/pypi/collectd

collectd.conf example:
    <LoadPlugin python>
        Globals true
    </LoadPlugin>

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

"""
__author__ = "Adrien Pujol - http://www.crashdump.fr/"
__copyright__ = "Copyright 2012, Adrien Pujol"
__license__ = "GPL"
__version__ = "0.2"
__email__ = "adrien.pujol@crashdump.fr"
__status__ = "Development"

import XenAPI
import collectd
import urllib
import os, sys, time, getopt
from xml.dom import minidom
from xml.parsers.expat import ExpatError


# Per VM dictionary (used by GetRRDUdpates to look up column numbers by variable names)
class VMReport(dict):
    """Used internally by GetRRDUdpates"""
    def __init__(self, uuid):
        self.uuid = uuid

# Per Host dictionary (used by GetRRDUdpates to look up column numbers by variable names)
class HostReport(dict):
    """Used internally by GetRRDUdpates"""
    def __init__(self, uuid):
        self.uuid = uuid

# Fetch and parse data class
class GetRRDUdpates:
    """ Object used to get and parse the output the http://host/rrd_udpates?..."""
    def __init__(self):
        # rrdParams are what get passed to the CGI executable in the URL
        self.rrdParams = dict()
        self.rrdParams['start'] = int(time.time()) - 1000
        self.rrdParams['host'] = 'true'   # include data for host (as well as for VMs)
        self.rrdParams['cf'] = 'AVERAGE'  # consolidation function, each sample averages 12 from the 5 second RRD
        self.rrdParams['interval'] = '10'
        self.rrdParams['output'] = 'collectd'

    def GetRows(self):
        return self.rows

    def GetVMList(self):
        return self.vm_reports.keys()

    def GetVMParamList(self, uuid):
        report = self.vm_reports[uuid]
        if not report:
            return []
        return report.keys()

    def GetHostUUID(self):
        report = self.host_report
        if not report:
            return None
        return report.uuid

    def GetHostParamList(self):
        report = self.host_report
        if not report:
            return []
        return report.keys()

    def GetHostData(self, param, row):
        report = self.host_report
        col = report[param]
        return self.__lookup_data(col, row)

    def GetRowTime(self, row):
        return self.__lookup_timestamp(row)

    # extract float from value (<v>) node by col,row
    def __lookup_data(self, col, row):
        # Note: the <rows> nodes are in reverse chronological order, and comprise
        # a timestamp <t> node, followed by self.columns data <v> nodes
        node = self.data_node.childNodes[self.rows - 1 - row].childNodes[col+1]
        return float(node.firstChild.toxml()) # node.firstChild should have nodeType TEXT_NODE

    # extract int from value (<t>) node by row
    def __lookup_timestamp(self, row):
        # Note: the <rows> nodes are in reverse chronological order, and comprise
        # a timestamp <t> node, followed by self.columns data <v> nodes
        node = self.data_node.childNodes[self.rows - 1 - row].childNodes[0]
        return int(node.firstChild.toxml()) # node.firstChild should have nodeType TEXT_NODE

    def Refresh(self, session, override_rrdParams = {}, server = 'http://localhost'):
        rrdParams = dict(self.rrdParams)
        rrdParams.update(override_rrdParams)
        rrdParams['host'] = "true"
        rrdParams['session_id'] = session
        rrdParamstr = "&".join(["%s=%s"  % (k,rrdParams[k]) for k in rrdParams])
        url = "%s/rrd_updates?%s" % (server, rrdParamstr)

        if rrdParams['output'] == "shell":
            print "Query: %s" % url
        # this is better than urllib.urlopen() as it raises an Exception on http 401 'Unauthorised' error
        # rather than drop into interactive mode
        sock = urllib.URLopener().open(url)
        xmlsource = sock.read()
        sock.close()
        xmldoc = minidom.parseString(xmlsource)
        self.__parse_xmldoc(xmldoc)
        # Update the time used on the next run
        self.rrdParams['start'] = self.end_time + 1 # avoid retrieving same data twice

    def __parse_xmldoc(self, xmldoc):

        # The 1st node contains meta data (description of the data)
        # The 2nd node contains the data
        self.meta_node = xmldoc.firstChild.childNodes[0]
        self.data_node = xmldoc.firstChild.childNodes[1]

        def LookupMetadataBytag(name):
            return int (self.meta_node.getElementsByTagName(name)[0].firstChild.toxml())

        # rows = number of samples per variable
        # columns = number of variables
        self.rows = LookupMetadataBytag('rows')
        self.columns = LookupMetadataBytag('columns')

        # These indicate the period covered by the data
        self.start_time = LookupMetadataBytag('start')
        self.step_time  = LookupMetadataBytag('step')
        self.end_time   = LookupMetadataBytag('end')

        # the <legend> Node describes the variables
        self.legend = self.meta_node.getElementsByTagName('legend')[0]

        # vm_reports matches uuid to per VM report
        self.vm_reports = {}

        # There is just one host_report and its uuid should not change!
        self.host_report = None

        # Handle each column.  (I.e. each variable)
        for col in range(self.columns):
            self.__handle_col(col)

    def __handle_col(self, col):
        # work out how to interpret col from the legend
        col_meta_data = self.legend.childNodes[col].firstChild.toxml()

        # vmOrHost will be 'vm' or 'host'.  Note that the Control domain counts as a VM!
        (cf, vmOrHost, uuid, param) = col_meta_data.split(':')

        if vmOrHost == 'vm':
            # Create a report for this VM if it doesn't exist
            if not self.vm_reports.has_key(uuid):
                self.vm_reports[uuid] = VMReport(uuid)

            # Update the VMReport with the col data and meta data
            vm_report = self.vm_reports[uuid]
            vm_report[param] = col

        elif vmOrHost == 'host':
            # Create a report for the host if it doesn't exist
            if not self.host_report:
                self.host_report = HostReport(uuid)
            elif self.host_report.uuid != uuid:
                raise PerfMonException, "Host UUID changed: (was %s, is %s)" % (self.host_report.uuid, uuid)

            # Update the HostReport with the col data and meta data
            self.host_report[param] = col

        else:
            raise PerfMonException, "Invalid string in <legend>: %s" % col_meta_data


class XenServerCollectd:
    def __init__(self):
        self.url = "http://127.0.0.1"
        self.user = "root"
        self.passwd = "password"
        self.verbose = False
        self.graphHost = True
        self.rrdParams = {}
        self.rrdParams['cf'] = "AVERAGE"
        self.rrdParams['start'] = int(time.time()) - 10
        self.rrdParams['interval'] = 5

    def SetConfig(self, conf):
        for node in conf.children:
            if node.key == 'Host':
                self.url = "http://%s" % node.values[0]
            elif node.key == 'User':
                self.user = node.values[0]
            elif node.key == 'Password':
                self.passwd = node.values[0]
            elif node.key == 'Verobse' :
                self.verbose = bool(node.values[0])

    def Send(self, uuid, metricsData, isHost):
        if isHost:
            hostname = 'xenserver-host-%s' % uuid
        else:
            hostname = 'xenserver-vm-%s' % uuid

        for key, value in metricsData.iteritems():
            cltd = collectd.Values();
            cltd.plugin = 'collectd-xenserver'
            cltd.host = hostname
            cltd.type = 'gauge' # http://linux.die.net/man/5/types.db
            cltd.values = [ value ]
            self.LogVerbose('Dispatching %s.%s %s' % (hostname, key, value))
            cltd.dispatch()

    def GetRows(self, uuid):
        result = {}
        for param in self.rrdUpdates.GetHostParamList():
                if param != '':
                    max_time=0
                    data=''
                    for row in range(self.rrdUpdates.GetRows()):
                        epoch = self.rrdUpdates.GetRowTime(row)
                        dv = str(self.rrdUpdates.GetHostData(param,row))
                        if epoch > max_time:
                            max_time = epoch
                            data = dv
                    result[param] = data
        return result

    def Connect(self):
        self.LogVerbose('Connecting: %s on %s' % (self.user, self.url))
        self.rrdUpdates = GetRRDUdpates()
        self.session = XenAPI.Session(self.url)
        self.session.xenapi.login_with_password(self.user, self.passwd)

    def Cycle(self):
        self.rrdUpdates.Refresh(self.session.handle, self.rrdParams, self.url)

        if self.graphHost:
            uuid = self.rrdUpdates.GetHostUUID()
            mectricsData = self.GetRows(uuid)
            isHost = True
            self.Send(uuid, mectricsData, isHost)

        for uuid in self.rrdUpdates.GetVMList():
            mectricsData = self.GetRows(uuid)
            isHost = False
            self.Send(uuid, mectricsData, isHost)

    def Disconnect(self):
        self.LogVerbose('Disnnecting: %s on %s' % (self.user, self.url))
        self.session.logout()

    def LogVerbose(self, msg):
        if not self.verbose:
            return
        collectd.info('xenserver-collectd [verbose]: %s' % msg)

# Hooks
xenserverCollectd = XenServerCollectd()
collectd.register_config(xenserverCollectd.SetConfig)
collectd.register_init(xenserverCollectd.Connect)
collectd.register_read(xenserverCollectd.Cycle)
collectd.register_shutdown(xenserverCollectd.Disconnect)
