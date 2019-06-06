import json
import logging

import netaddr

import random

from urllib import unquote

from powergslb.server.http.handler.abstract import AbstractContentHandler

import powergslb.monitor
import powergslb.database

__all__ = ['PowerDNSContentHandler']


class PowerDNSContentHandler(AbstractContentHandler):
    """
    PowerDNS content handler
    """
    _lb_topology_map = {}

    def _filter_records(self, qtype_records):
        records = []

        for qtype in qtype_records:
            lb_method = 'p'
            if len(qtype_records[qtype]) > 0 and 'lbmethod' in qtype_records[qtype][0] and qtype_records[qtype][0]['lbmethod'] != None:
                lb_method = qtype_records[qtype][0]['lbmethod']

            logging.debug('qtype_records: %s', str(qtype_records))
            logging.debug('LB Method: %s', lb_method)

            if lb_method == 'p':
                filtered_records = self._lb_priority( qtype_records[qtype] )
                filtered_records = self._lb_randomize( filtered_records )
            elif lb_method == 'wrr':
                filtered_records = self._lb_wrr( qtype_records[qtype] )
            elif lb_method == 't':
                filtered_records = self._lb_topology( qtype_records[qtype] )
                filtered_records = self._lb_randomize( filtered_records )
            elif lb_method == 'tp':
                filtered_records = self._lb_topology( qtype_records[qtype] )
                filtered_records = self._lb_priority( filtered_records )
            elif lb_method == 'twrr':
                filtered_records = self._lb_topology( qtype_records[qtype] )
                filtered_records = self._lb_wrr( filtered_records )
            elif lb_method == 'ltd':
                filtered_records = self._lb_ltd( qtype_records[qtype] )
            elif lb_method == 'persistence':
                filtered_records = self._lb_persitence( qtype_records[qtype] )
            else:
                filtered_records = self._lb_priority( qtype_records[qtype] )

            if not filtered_records:
                continue

            if filtered_records[0]['persistence']:
                records.append(self._remote_ip_persistence(filtered_records))
            else:
                records.extend(filtered_records)

        return records

    def _get_lookup(self):
        v3_format = True
        self.dirs[2]=unquote(self.dirs[2])
        if self.dirs[2].endswith('.'):
            v3_format = False
            self.dirs[2] = self.dirs[2].rstrip('.')

        records = self.database.gslb_records(*self.dirs[2:])
        qtype_records = self._split_records(records, v3_format)
        filtered_records = self._filter_records(qtype_records)
        return self._strip_records(filtered_records, v3_format)

    def _is_in_view(self, record):
        result = False
        try:
            result = bool(netaddr.smallest_matching_cidr(self.remote_ip, record.get('rule').split()))
        except (AttributeError, netaddr.AddrFormatError, ValueError) as e:
            logging.error('{}: record id {} view rule invalid: {}: {}'.format(
                type(self).__name__, record['id'], type(e).__name__, e))

        return result

    def _remote_ip_persistence(self, records):
        persistence_value = netaddr.IPAddress(self.remote_ip).value >> records[0]['persistence']
        return records[hash(persistence_value) % len(records)]

    def _split_records(self, records, v3_format=False):
        qtype_records = {}
        for record in records:
            if v3_format and record['qtype'] in ['MX', 'SRV']:
                content_split = record['content'].split()
                try:
                    record['priority'] = int(content_split[0])
                    record['content'] = ' '.join(content_split[1:])
                except (KeyError, ValueError) as e:
                    logging.error('{}: record id {} priority missing or invalid: {}: {}'.format(
                        type(self).__name__, record['id'], type(e).__name__, e))
                    continue

            if record['qtype'] not in qtype_records:
                qtype_records[record['qtype']] = []

            qtype_records[record['qtype']].append(record)

        return qtype_records

    def _lb_priority(self, records):
      fallback_records = {}
      live_records = {}

      for record in records:
          if not self._is_in_view(record):
            continue

          if record['fallback']:
              if record['weight'] not in fallback_records:
                fallback_records[record['weight']] = []

              fallback_records[record['weight']].append(record)

          if record['id'] not in powergslb.monitor.get_status():
              if record['weight'] not in live_records:
                live_records[record['weight']] = []

              live_records[record['weight']].append(record)

      if live_records:
          filtered_records = live_records[max(live_records)]
      elif fallback_records:
          filtered_records = fallback_records[max(fallback_records)]
      else:
          filtered_records = []

      return filtered_records

    def _lb_wrr(self, records):
      fallback_records = {}
      live_records = {}

      nrecords = len(records)
      sum_weight = 0
      sum_weight_fallback = 0
      for record in records:
        if not record['fallback']:
          sum_weight += record['weight']
        else:
          sum_weight_fallback += record['weight']

      # Live records
      if sum_weight > 0 :
        rand = random.random()
        proba_max = 0.0
        for record in records:
          if not self._is_in_view(record) or record['fallback']:
              continue
          if record['id'] not in powergslb.monitor.get_status():
            proba_max = proba_max + float(record['weight'])/float(sum_weight)
            logging.debug("live_records - sum_weight: %d - rand: %f - record['weight']: %d - proba_max = w+rw/s: %f", sum_weight, rand, record['weight'], proba_max)
            if proba_max >= rand:
              if '0' not in live_records:
                live_records['0'] = []
              live_records['0'].append(record)
              break

      else:
        live_records['0'] = []
        for record in records:
          if not self._is_in_view(record):
              continue
          if not record['fallback'] and record['id'] not in powergslb.monitor.get_status():
            live_records['0'].append(record)

      # Fallback records
      if sum_weight_fallback > 0:
        rand = random.random()
        proba_max = 0.0
        for record in records:
          if not self._is_in_view(record) and not record['fallback']:
              continue

          proba_max = proba_max + float(record['weight'])/float(sum_weight)
          logging.debug("fallback_records - sum_weight: %d - rand: %f - record['weight']: %d - proba_max = w+rw/s: %f", sum_weight, rand, record['weight'], proba_max)
          if proba_max >= rand:
            if '0' not in fallback_records:
              fallback_records['0'] = []
            fallback_records['0'].append(record)
            break

      else:
        fallback_records['0'] = []
        for record in records:
          if not self._is_in_view(record):
              continue
          if record['fallback']:
            fallback_records['0'].append(record)

      # Final record list
      if live_records:
          filtered_records = live_records[max(live_records)]
      elif fallback_records:
          filtered_records = fallback_records[max(fallback_records)]
      else:
          filtered_records = []

      return filtered_records

    def _lb_topology( self, records):

      if len(records) > 0 and 'lboption_json' in records[0] and records[0]['lboption_json'] != None:
        try:
          self._lb_topology_map = json.loads( records[0]['lboption_json'] )
        except ValueError:
          logging.error( "Unable to load topology map: %s !!!", str(records[0]['lboption_json']) )
        logging.debug( "Topology map: %s", str(self._lb_topology_map) )

      client_region = self._lb_get_topology_region( self.remote_ip )

      logging.debug("= TOPOLOGY = ip: %s - region: %s", self.remote_ip, client_region)

      if client_region == '':
        return records

      nfallback_records = 0
      nlive_records = 0
      topology_fallback_records = []
      topology_live_records = []
      for record in records:
        logging.debug("= TOPOLOGY - _lb_topology = record: %s", str(record))

        if not self._is_in_view(record) or (record['qtype'] != 'A' and record['qtype'] != 'AAAA'):
          continue

        if record['fallback']:
          record_region = self._lb_get_topology_region( record['content'] )
          if record_region == client_region:
            topology_fallback_records.append( record )

        if record['id'] not in powergslb.monitor.get_status():
          record_region = self._lb_get_topology_region( record['content'] )
          logging.debug("= TOPOLOGY = record content: %s - region: %s", record['content'], record_region)
          if record_region == client_region:
            topology_live_records.append( record )

      # Final record list
      if topology_live_records:
        return topology_live_records

      return records

    def _lb_get_topology_region( self, ip):
      logging.debug("= TOPOLOGY - _lb_get_topology_region = ip: %s", ip)
      ip = netaddr.IPAddress( ip ).value
      region = ''

      logging.debug("= TOPOLOGY - _lb_get_topology_region = ip: %s", ip)

      for region_name, net_list in self._lb_topology_map.iteritems():
        if region != '':
          break
        for net in net_list:
          network = netaddr.IPNetwork( net )
          if ip >= network.first and ip <= network.last:
            region = region_name
            break

      return region

    def _lb_randomize(self, records):
      random.shuffle( records )
      return records

    def _lb_ltd( self, records):
      logging.debug( "alain: %s",str(records) )

      ts = powergslb.database.TimeSeries( **powergslb.system.get_config().items('redis') )

      filtered_record = []
      avg_td = 0.0
      for record in records:
        avg_td_tmp = ts.get_response_time_avg( record['id'] )
        logging.debug(' ltd - content: %s - avg_td: %f - avg_ltd: %f', record['content'], avg_td_tmp, avg_td)

        if len( filtered_record ) == 0:
          filtered_record.append( record )
          avg_td = avg_td_tmp
        elif avg_td_tmp < avg_td and avg_td_tmp > 0.0:
          filtered_record[0] = record
          avg_td = avg_td_tmp

      return filtered_record

    @staticmethod
    def _strip_records(records, v3_format=False):
        result = []
        for record in records:
            if v3_format and record['qtype'] in ['MX', 'SRV']:
                names = ['qname', 'qtype', 'content', 'ttl', 'priority']
                values = [record['qname'], record['qtype'], record['content'], record['ttl'], record['priority']]
            else:
                names = ['qname', 'qtype', 'content', 'ttl']
                values = [record['qname'], record['qtype'], record['content'], record['ttl']]

            result.append(dict(zip(names, values)))

        return result

    def content(self):
        if len(self.dirs) == 4 and self.dirs[1] == 'lookup':
            content = {'result': self._get_lookup()}
        else:
            content = {'result': False}

        return json.dumps(content, separators=(',', ':'))
