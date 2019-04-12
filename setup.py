#!/usr/bin/env python2.7

from distutils.core import setup

powergslb = {}
execfile('src/powergslb/__init__.py', powergslb)

setup(name='PowerGSLB',
      version=powergslb['__version__'],
      author='Aleksey Chudov',
      author_email='aleksey.chudov@gmail.com',
      url='https://github.com/AlekseyChudov/powergslb',
      description='PowerDNS Remote GSLB Backend',
      long_description='PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution',
      package_dir={'': 'src'},
      platforms=['Linux'],
      license='MIT',
      requires=['mysql', 'netaddr', 'pyping', 'subprocess32', 'systemd.daemon'],
      packages=[
          'powergslb',
          'powergslb.database',
          'powergslb.database.mysql',
          'powergslb.database.redis',
          'powergslb.monitor',
          'powergslb.server',
          'powergslb.server.http',
          'powergslb.server.http.handler',
          'powergslb.system'
      ])
