#!/bin/python

import sys

import logging

import pycurl
import requests
from requests.auth import HTTPBasicAuth

from StringIO import StringIO

import json

import dns.resolver
import dns.name
import dns.message
import dns.query
import dns.flags

import time

#
#
# LOG
#
#
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
#ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
logger.addHandler(ch)

#
#
# API
#
#
powergslb_url = 'http://127.0.0.1:8080/dns/lookup/'
powergslb_admin_url = 'http://127.0.0.1:8080/admin/'
powergslb_api_url = 'http://127.0.0.1:8080/admin/w2ui'
powergslb_admin_auth="admin:admin"

#
#
# DNS
#
#
gslb_dns_check = False
gslb_resolver = dns.resolver.Resolver()
gslb_resolver.nameservers = ['127.0.0.1']

external_dns_check = False
external_resolver = dns.resolver.Resolver()
external_resolver.nameservers = ['8.8.8.8', '4.4.4.4']

#
#
# CONFIG
#
#
lb_config = {
  'options': [
    { 'lbmethod': 't', 'lboption': 't_testauto.local', 'lboption_json': '{"region1": ["10.150.0.0/16"], "region2": ["10.160.0.0/16"]}'}
  ]
}

test_config = {
  'p': {
    'A': {
      'domain': 'testauto.local.',
      'name': 'p-a.testauto.local.',
      'records': [
        { 'type': 'A', 'content': '10.150.0.10', 'weight': '1', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.150.0.11', 'weight': '1', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.10', 'weight': '2', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.11', 'weight': '2', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
      ],
    },
    'CNAME': {
      'domain': 'testauto.local.',
      'name': 'p-cname.testauto.local.',
      'records': [
        { 'type': 'CNAME', 'content': 's1.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's2.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's3.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's4.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 'p', 'monitor': 'No check', 'view': 'Public' },
      ]
    }
  },
  't': {
    'A': {
      'domain': 'testauto.local.',
      'name': 't-a.testauto.local.',
      'records': [
        { 'type': 'A', 'content': '10.150.0.10', 'weight': '1', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.150.0.11', 'weight': '1', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.10', 'weight': '2', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.11', 'weight': '2', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
      ],
    },
    'CNAME': {
      'domain': 'testauto.local.',
      'name': 't-cname.testauto.local.',
      'records': [
        { 'type': 'CNAME', 'content': 's1.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's2.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's3.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's4.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 't', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
      ]
    }
  },
  'wrr': {
    'A': {
      'domain': 'testauto.local.',
      'name': 'wrr-a.testauto.local.',
      'records': [
        { 'type': 'A', 'content': '10.150.0.10', 'weight': '1', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.150.0.11', 'weight': '1', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.10', 'weight': '2', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.11', 'weight': '2', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
      ],
    },
    'CNAME': {
      'domain': 'testauto.local.',
      'name': 'wrr-cname.testauto.local.',
      'records': [
        { 'type': 'CNAME', 'content': 's1.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's2.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's3.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's4.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 'wrr', 'monitor': 'No check', 'view': 'Public' },
      ]
    }
  },
  'twrr': {
    'A': {
      'domain': 'testauto.local.',
      'name': 'twrr-a.testauto.local.',
      'records': [
        { 'type': 'A', 'content': '10.150.0.10', 'weight': '1', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.150.0.11', 'weight': '1', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.10', 'weight': '2', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'A', 'content': '10.160.0.11', 'weight': '2', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
      ],
    },
    'CNAME': {
      'domain': 'testauto.local.',
      'name': 'twrr-cname.testauto.local.',
      'records': [
        { 'type': 'CNAME', 'content': 's1.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's2.testauto.local.', 'weight': '1', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's3.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
        { 'type': 'CNAME', 'content': 's4.testauto.local.', 'weight': '2', 'ttl': 1, 'lbmethod': 'twrr', 'lboptions': 't_testauto.local', 'monitor': 'No check', 'view': 'Public' },
      ]
    }
  },
}

###############################################################

def curl_request( record_name='testauto.local', record_type='ANY', client_ip='127.0.0.1', vhost='powergslb.local' ):

  buffer = StringIO()
  curl = pycurl.Curl()
  #c.setopt(c.VERBOSE, True)
  curl.setopt(curl.URL, str(powergslb_url) + str(record_name) + './' + str(record_type) )
  curl.setopt(curl.WRITEDATA, buffer)

  #curl.setopt(curl.WRITEFUNCTION, response.write)
  curl.setopt(curl.HTTPHEADER, ['X-Remotebackend-Real-Remote: ' + client_ip, 'host: ' + vhost])
  curl.perform()
  body = buffer.getvalue()
  curl.close()

  return body

def api_nqueries( record_name, record_type='ANY', client_ip='127.0.0.1', nqueries=100 ):
  i = 0
  count = {}
  while i < nqueries:
    body = curl_request( record_name, record_type, client_ip )
    logger.debug("%s", body)

    response = json.loads(body)
    for record in response['result']:
      if not record['content'] in count:
        count[ record['content'] ] = 1
      else:
        count[ record['content'] ] += 1

    i += 1

  return count

def dns_nqueries( record_name, record_type='A', nqueries=100, sleep=0 ):
  i = 0
  count = {}
  while i < nqueries:
    answers = gslb_resolver.query(record_name, record_type)

    for rdata in answers:
      #logger.info("%d - answer: %s", i, str(rdata))
      if record_type == 'A':
        key = rdata.address
      elif record_type == 'CNAME':
        key = rdata.target

      logger.debug("key: %s", str(key) )
      if not str(key) in count:
        count[ str(key) ] = 1
      else:
        count[ str(key) ] += 1

    if sleep > 0:
      time.sleep( sleep )
    i += 1

  return count

###############################################################

##### IMPORT DATA #####

def import_data():
  # LB Parameters
  for lboption in lb_config['options']:
    # Create Domain
    data = {
      'cmd': 'save-record',
      'data': 'lboptions',
      'recid': 0,
      'record[lbmethod]': lboption['lbmethod'],
      'record[lboption]': lboption['lboption'],
      'record[lboption_json]': lboption['lboption_json'],
    }
    r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
    logging.debug("%d- %s",r.status_code,r.text)
    if r.status_code != 200:
      exit(0)

  # Domais / Records
  for lbmethod,lbrecords  in test_config.iteritems():
    logging.info('lbmethod: %s', str(lbmethod) )

    for recordtype, records in lbrecords.iteritems():
      logging.debug('recordtype: %s', str(recordtype) )

      # Create Domain
      data = {
        'cmd': 'save-record',
        'data': 'domains',
        'recid': 0,
        'record[domain]': records['domain']
      }
      r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
      logging.debug("%d- %s",r.status_code,r.text)
      if r.status_code != 200:
        exit(0)
      # Create SOA record
      data = {
        'cmd': 'save-record',
        'data': 'records',
        'recid': 0,
        'record[domain]': records['domain'],
        'record[name]': records['domain'],
        'record[name_type]': 'SOA',
        'record[content]': 'ns1.' + records['domain'] + ' hostmaster.' + records['domain'] + ' 2016010101 21600 3600 1209600 300',
        'record[ttl]': 86400,
        'record[disabled]': 0,
        'record[fallback]': 0,
        'record[persistence]': 0,
        'record[weight]': 0,
        'record[monitor]': 'No check',
        'record[view]': 'Public',
      }
      r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
      logging.debug("%d- %s",r.status_code,r.text)
      if r.status_code != 200:
        exit(0)

      # Create Records
      for record in records['records']:
        logging.debug('record: %s', str(record))
        # Create Records
        data = {
          'cmd': 'save-record',
          'data': 'records',
          'recid': 0,
        }

        data['record[domain]'] = records['domain']
        data['record[name]'] = records['name']

        if 'type' in record:
          data['record[name_type]'] = record['type']
        if 'content' in record:
          data['record[content]'] = record['content']
        if 'ttl' in record:
          data['record[ttl]'] = record['ttl']
        if 'disabled' in record:
          data['record[disabled]'] = record['disabled']
        if 'fallback' in record:
          data['record[fallback]'] = record['fallback']
        if 'persistence' in record:
          data['record[persistence]'] = record['persistence']
        if 'weight' in record:
          data['record[weight]'] = record['weight']
        if 'lbmethod' in record:
          data['record[lbmethod]'] = record['lbmethod']
        if 'lboption' in record:
          data['record[lboption]'] = record['lboption']
        if 'monitor' in record:
          data['record[monitor]'] = record['monitor']
        if 'view' in record:
          data['record[view]'] = record['view']

        logging.debug('data: %s', str(data))

        r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
        logging.debug("%d- %s",r.status_code,r.text)
        if r.status_code != 200:
          exit(0)

  return True

def delete_data():
  for lbmethod,lbrecords  in test_config.iteritems():
    logging.debug('lbmethod: %s', str(lbmethod) )

    for recordtype, records in lbrecords.iteritems():
      logging.debug('recordtype: %s', str(recordtype) )

      # Del records
      for record in records['records']:
        logging.debug('============================= record: %s', str(record))
        # Create Records
        data = {
          'cmd': 'get-records',
          'data': 'records',

          'search[0][field]': 'domain',
          'search[0][type]': 'text',
          'search[0][operator]': 'is',
          'search[0][value]': records['domain'],

          'search[1][field]': 'name',
          'search[1][type]': 'text',
          'search[1][operator]': 'is',
          'search[1][value]': records['name'],

          'search[2][field]': 'content',
          'search[2][type]': 'text',
          'search[2][operator]': 'is',
          'search[2][value]': record['content'],

          'searchLogic': 'AND',
          'sort[0][field]': 'recid',
          'sort[0][direction]': 'asc',
          'limit': 100,
          'offset': 0
        }

        logging.debug('data: %s', str(data))

        r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
        logging.debug("%d- %s",r.status_code,r.text)
        if r.status_code != 200:
          exit(0)

        logging.debug("r.text: %s", str(r.text) )
        rjson = json.loads( r.text )
        for rec2del in rjson['records']:
          logging.debug("rec2del: %s", str(rec2del) )
          data = {
            'cmd': 'delete-records',
            'selected[]': rec2del['recid'],
            'data': 'records',
            'search[0][field]': 'domain',
            'search[0][type]': 'text',
            'search[0][operator]': 'is',
            'search[0][value]': records['domain'],
            'search[1][field]': 'content',
            'search[1][type]': 'text',
            'search[1][operator]': 'is',
            'search[1][value]': record['content'],
            'searchLogic': 'AND',
            'sort[0][field]': 'recid',
            'sort[0][direction]': 'asc',
            'limit': 100,
            'offset': 0
          }
          logging.debug('data: %s', str(data))

          r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
          logging.debug("%d- %s",r.status_code,r.text)
          if r.status_code != 200:
            exit(0)

    # Delete Domain
    data = {
      'cmd': 'get-records',
      'data': 'domains',
      'search[0][field]': 'domain',
      'search[0][type]': 'text',
      'search[0][operator]': 'is',
      'search[0][value]': records['domain'],
      'searchLogic': 'AND',
      'sort[0][field]': 'recid',
      'sort[0][direction]': 'asc',
      'limit': 100,
      'offset': 0
    }
    r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
    logging.debug("%d- %s",r.status_code,r.text)
    if r.status_code != 200:
      exit(0)

    logging.debug("r.text: %s", str(r.text) )
    rjson = json.loads( r.text )
    d2del = rjson['records'][0]
    logging.debug("d2del: %s", str(d2del) )
    data = {
      'cmd': 'delete-records',
      'selected[]': d2del['recid'],
      'data': 'domains',
      'search[0][field]': 'domain',
      'search[0][type]': 'text',
      'search[0][operator]': 'is',
      'search[0][value]': records['domain'],
      'searchLogic': 'AND',
      'sort[0][field]': 'recid',
      'sort[0][direction]': 'asc',
      'limit': 100,
      'offset': 0
    }
    logging.debug('data: %s', str(data))

    r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
    logging.debug("%d- %s",r.status_code,r.text)
    if r.status_code != 200:
      exit(0)

  # Delete LB Parameters
  for lboption in lb_config['options']:
    data = {
      'cmd': 'get-records',
      'data': 'lboptions',
      'recid': 0,
      'search[0][field]': 'lboption',
      'search[0][type]': 'text',
      'search[0][operator]': 'is',
      'search[0][value]': lboption['lboption'],
      'searchLogic': 'AND',
      'sort[0][field]': 'recid',
      'sort[0][direction]': 'asc',
      'limit': 100,
      'offset': 0
    }
    r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
    logging.info("%d- %s",r.status_code,r.text)
    if r.status_code != 200:
      exit(0)

    logging.info("r.text: %s", str(r.text) )
    rjson = json.loads( r.text )
    lbo2del = rjson['records']
    logging.info("lbo2del: %s", str(lbo2del) )
    data = {
      'cmd': 'delete-records',
      'selected[]': lbo2del[0]['recid'],
      'data': 'lboptions',
      'search[0][field]': 'lboption',
      'search[0][type]': 'text',
      'search[0][operator]': 'is',
      'search[0][value]': lboption['lboption'],
      'searchLogic': 'AND',
      'sort[0][field]': 'recid',
      'sort[0][direction]': 'asc',
      'limit': 100,
      'offset': 0
    }
    logging.debug('data: %s', str(data))

    r = requests.post(powergslb_api_url, data=data, auth=HTTPBasicAuth('admin', 'admin'))
    logging.debug("%d- %s",r.status_code,r.text)
    if r.status_code != 200:
      exit(0)

  return True

###############################################################

##### IMPORT DATA #####
import_data()

##### PRIORITY #####

logger.info("=== Priority")
logger.info("=== Priority ---> A records")

nqueries = 100
count = api_nqueries( 'p-a.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if '10.160.0.10' in count and count['10.160.0.10'] == nqueries and '10.160.0.11' in count and count['10.160.0.11'] == nqueries:
  logging.info("=== Priority ---> A records ---> OK - count: %s", str(count))
else:
  logging.error("=== Priority ---> A records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'p-a.testauto.local', 'A', nqueries=100 )

  if '10.160.0.10' in dns_count and dns_count['10.160.0.10'] == nqueries and '10.160.0.11' in dns_count and dns_count['10.160.0.11'] == nqueries:
    logging.info("=== Priority ---> A records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Priority ---> A records ---> KO - dns_count: %s", str(dns_count))

###

logger.info("=== Priority")
logger.info("=== Priority ---> CNAME records")

nqueries = 100
count = api_nqueries( 'p-cname.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if ('s3.testauto.local.' in count and count['s3.testauto.local.'] == nqueries and not 's4.testauto.local.' in count) or ('s4.testauto.local.' in count and count['s4.testauto.local.'] == nqueries and not 's3.testauto.local.' in count):
  logging.info("=== Priority ---> CNAME records ---> OK - count: %s", str(count))
else:
  logging.error("=== Priority ---> CNAME records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'p-cname.testauto.local', 'CNAME', nqueries=100 )

  if ('s3.testauto.local.' in dns_count and dns_count['s3.testauto.local.'] == nqueries and not 's4.testauto.local.' in dns_count) or ('s4.testauto.local.' in dns_count and dns_count['s4.testauto.local.'] == nqueries and not 's3.testauto.local.' in dns_count):
    logging.info("=== Priority ---> CNAME records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Priority ---> CNAME records ---> KO - dns_count: %s", str(dns_count))

##### Topology #####

logger.info("=== Topology")
logger.info("=== Topology ---> A records")

nqueries = 100
count = api_nqueries( 't-a.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if '10.150.0.10' in count and count['10.150.0.10'] == nqueries and '10.150.0.11' in count and count['10.150.0.11'] == nqueries and '10.160.0.10' in count and count['10.160.0.10'] == nqueries and '10.160.0.11' in count and count['10.160.0.11'] == nqueries:
  logging.info("=== Topology ---> A records ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology ---> A records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 't-a.testauto.local', 'A', nqueries=100 )

  if '10.150.0.10' in dns_count and dns_count['10.150.0.10'] == nqueries and '10.150.0.11' in dns_count and dns_count['10.150.0.11'] == nqueries and '10.160.0.10' in dns_count and dns_count['10.160.0.10'] == nqueries and '10.160.0.11' in dns_count and dns_count['10.160.0.11'] == nqueries:
    logging.info("=== Topology ---> A records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology ---> A records ---> KO - dns_count: %s", str(dns_count))

###

logging.info("=== Topology ---> A records ---> client IP 10.150.0.100")

nqueries = 100
count = api_nqueries( 't-a.testauto.local', 'ANY', client_ip='10.150.0.100', nqueries=100 )

if '10.150.0.10' in count and count['10.150.0.10'] == nqueries and '10.150.0.11' in count and count['10.150.0.11'] == nqueries and not '10.160.0.10' in count and not '10.160.0.11' in count:
  logging.info("=== Topology ---> A records ---> client IP 10.150.0.100 ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology ---> A records ---> client IP 10.150.0.100 ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 't-a.testauto.local', 'A', nqueries=100 )

  if '10.150.0.10' in dns_count and dns_count['10.150.0.10'] == nqueries and '10.150.0.11' in dns_count and dns_count['10.150.0.11'] == nqueries and not '10.160.0.10' in dns_count and not '10.160.0.11' in dns_count:
    logging.info("=== Topology ---> A records ---> client IP 10.150.0.100 ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology ---> A records ---> client IP 10.150.0.100 ---> KO - dns_count: %s", str(dns_count))

###

logger.info("=== Topology ---> CNAME records ")

nqueries = 100
count = api_nqueries( 't-cname.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if count['s1.testauto.local.'] == nqueries and count['s2.testauto.local.'] == nqueries and count['s3.testauto.local.'] == nqueries and count['s4.testauto.local.'] == nqueries:
  logging.info("=== Topology ---> CNAME records ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology ---> CNAME records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 't-cname.testauto.local', 'CNAME', nqueries=100 )

  if dns_count['s1.testauto.local.'] == nqueries and dns_count['s2.testauto.local.'] == nqueries and dns_count['s3.testauto.local.'] == nqueries and dns_count['s4.testauto.local.'] == nqueries:
    logging.info("=== Topology ---> CNAME records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology ---> CNAME records ---> KO - dns_count: %s", str(dns_count))

###

logger.info("=== Topology ---> CNAME records ---> client IP 10.150.0.100")

nqueries = 100
count = api_nqueries( 't-cname.testauto.local', 'ANY', client_ip='10.150.0.100', nqueries=100 )

if count['s1.testauto.local.'] == nqueries and count['s2.testauto.local.'] == nqueries and count['s3.testauto.local.'] == nqueries and count['s4.testauto.local.'] == nqueries:
  logging.info("=== Topology ---> CNAME records ---> client IP 10.150.0.100 ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology ---> CNAME records ---> client IP 10.150.0.100 ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 't-cname.testauto.local', 'CNAME', nqueries=100 )

  if dns_count['s1.testauto.local.'] == nqueries and dns_count['s2.testauto.local.'] == nqueries and dns_count['s3.testauto.local.'] == nqueries and dns_count['s4.testauto.local.'] == nqueries:
    logging.info("=== Topology ---> CNAME records ---> client IP 10.150.0.100 ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology ---> CNAME records ---> client IP 10.150.0.100 ---> KO - dns_count: %s", str(dns_count))

##### Weighted Round Robin #####

logger.info("=== Weighted Round Robin")
logger.info("=== Weighted Round Robin ---> A records")

nqueries = 100
count = api_nqueries( 'wrr-a.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if count['10.150.0.10'] < count['10.160.0.10'] and count['10.150.0.10'] < count['10.160.0.11'] and count['10.150.0.11'] < count['10.160.0.10'] and count['10.150.0.11'] < count['10.160.0.11'] and count['10.150.0.10'] + count['10.150.0.11'] < 40 and count['10.150.0.10'] + count['10.150.0.11'] + count['10.160.0.10'] + count['10.160.0.11'] == nqueries:
  logging.info("=== Weighted Round Robin ---> A records ---> OK - count: %s", str(count))
else:
  logging.error("=== Weighted Round Robin ---> A records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'wrr-a.testauto.local', 'A', nqueries=100, sleep=1 )

  if dns_count['10.150.0.10'] < dns_count['10.160.0.10'] and dns_count['10.150.0.10'] < dns_count['10.160.0.11'] and dns_count['10.150.0.11'] < dns_count['10.160.0.10'] and dns_count['10.150.0.11'] < dns_count['10.160.0.11'] and dns_count['10.150.0.10'] + dns_count['10.150.0.11'] < 40 and dns_count['10.150.0.10'] + dns_count['10.150.0.11'] + dns_count['10.160.0.10'] + dns_count['10.160.0.11'] == nqueries:
    logging.info("=== Weighted Round Robin ---> A records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Weighted Round Robin ---> A records ---> KO - dns_count: %s", str(dns_count))

###

logger.info("=== Weighted Round Robin ---> CNAME records")

nqueries = 100
count = api_nqueries( 'wrr-cname.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if count['s1.testauto.local.'] < count['s3.testauto.local.'] and count['s1.testauto.local.'] < count['s4.testauto.local.'] and count['s2.testauto.local.'] < count['s3.testauto.local.'] and count['s2.testauto.local.'] < count['s4.testauto.local.'] and count['s1.testauto.local.'] + count['s2.testauto.local.'] < 40 and count['s1.testauto.local.'] + count['s2.testauto.local.'] + count['s3.testauto.local.'] + count['s4.testauto.local.'] == nqueries:
  logging.info("=== Weighted Round Robin ---> CNAME records ---> OK - count: %s", str(count))
else:
  logging.error("=== Weighted Round Robin ---> CNAME records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'wrr-cname.testauto.local', 'CNAME', nqueries=100, sleep=1 )

  if dns_count['s1.testauto.local.'] < dns_count['s3.testauto.local.'] and dns_count['s1.testauto.local.'] < dns_count['s4.testauto.local.'] and dns_count['s2.testauto.local.'] < dns_count['s3.testauto.local.'] and dns_count['s2.testauto.local.'] < dns_count['s4.testauto.local.'] and dns_count['s1.testauto.local.'] + dns_count['s2.testauto.local.'] < 40 and dns_count['s1.testauto.local.'] + dns_count['s2.testauto.local.'] + dns_count['s3.testauto.local.'] + dns_count['s4.testauto.local.'] == nqueries:
    logging.info("=== Weighted Round Robin ---> CNAME records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Weighted Round Robin ---> CNAME records ---> KO - dns_count: %s", str(dns_count))

##### Weighted Round Robin #####

logger.info("=== Topology Weighted Round Robin")
logger.info("=== Topology Weighted Round Robin ---> A records")

nqueries = 100
count = api_nqueries( 'twrr-a.testauto.local', 'ANY', client_ip='127.0.0.1', nqueries=100 )

if ( count['10.150.0.10'] < count['10.160.0.10'] and count['10.150.0.10'] < count['10.160.0.11'] and count['10.150.0.11'] < count['10.160.0.10'] and count['10.150.0.11'] < count['10.160.0.11'] and count['10.150.0.10'] + count['10.150.0.11'] < 40 ):
  logging.info("=== Topology Weighted Round Robin ---> A records ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology Weighted Round Robin ---> A records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'twrr-a.testauto.local', 'A', nqueries=100, sleep=1 )

  logging.info("=== Topology Weighted Round Robin ---> A records ---> dns_count: %s", str(dns_count))
  if ( '10.150.0.10' in dns_count and '10.160.0.10' in dns_count and dns_count['10.150.0.10'] < dns_count['10.160.0.10'] and dns_count['10.150.0.10'] < dns_count['10.160.0.11'] and dns_count['10.150.0.11'] < dns_count['10.160.0.10'] and dns_count['10.150.0.11'] < dns_count['10.160.0.11'] and dns_count['10.150.0.10'] + dns_count['10.150.0.11'] < 40 ):
    logging.info("=== Topology Weighted Round Robin ---> A records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology Weighted Round Robin ---> A records ---> KO - dns_count: %s", str(dns_count))

###

logger.info("=== Topology Weighted Round Robin ---> A records ---> client IP 10.150.0.100")

nqueries = 100
count = api_nqueries( 'twrr-a.testauto.local', 'ANY', client_ip='10.150.0.100', nqueries=100 )

if ( not '10.160.0.11' in count and not '10.160.0.10' in count and count['10.150.0.10'] + count['10.150.0.11'] == nqueries ):
  logging.info("=== Topology Weighted Round Robin ---> A records ---> client IP 10.150.0.100 ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology Weighted Round Robin ---> A records ---> client IP 10.150.0.100 ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'twrr-a.testauto.local', 'A', nqueries=100, sleep=1 )

  if ( not '10.160.0.11' in dns_count and not '10.160.0.10' in dns_count and dns_count['10.150.0.10'] + dns_count['10.150.0.11'] == nqueries ):
    logging.info("=== Topology Weighted Round Robin ---> A records ---> client IP 10.150.0.100 ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology Weighted Round Robin ---> A records ---> client IP 10.150.0.100 ---> KO - dns_count: %s", str(dns_count))

###

logger.info("=== Topology Weighted Round Robin ---> CNAME records")

nqueries = 100
count = api_nqueries( 'twrr-cname.testauto.local', 'ANY', client_ip='10.150.0.100', nqueries=100 )

if ( count['s1.testauto.local.'] < count['s3.testauto.local.'] and count['s1.testauto.local.'] < count['s4.testauto.local.'] and count['s2.testauto.local.'] < count['s3.testauto.local.'] and count['s2.testauto.local.'] < count['s4.testauto.local.'] and count['s1.testauto.local.'] + count['s2.testauto.local.'] < 40 ):
  logging.info("=== Topology Weighted Round Robin ---> CNAME records ---> OK - count: %s", str(count))
else:
  logging.error("=== Topology Weighted Round Robin ---> CNAME records ---> KO - count: %s", str(count))

if gslb_dns_check:
  dns_count = dns_nqueries( 'twrr-cname.testauto.local', 'CNAME', nqueries=100, sleep=1 )

  if ( dns_count['s1.testauto.local.'] < dns_count['s3.testauto.local.'] and dns_count['s1.testauto.local.'] < dns_count['s4.testauto.local.'] and dns_count['s2.testauto.local.'] < dns_count['s3.testauto.local.'] and dns_count['s2.testauto.local.'] < dns_count['s4.testauto.local.'] and dns_count['s1.testauto.local.'] + dns_count['s2.testauto.local.'] < 40 ):
    logging.info("=== Topology Weighted Round Robin ---> CNAME records ---> OK - dns_count: %s", str(dns_count))
  else:
    logging.error("=== Topology Weighted Round Robin ---> CNAME records ---> KO - dns_count: %s", str(dns_count))

##### DATA CLEANUP #####
delete_data()