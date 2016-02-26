#!/usr/bin/env python

from distutils.core import setup

setup(name='PowerGSLB',
      version='1.4.2',
      author='Aleksey Chudov',
      author_email='aleksey.chudov@gmail.com',
      url='https://github.com/AlekseyChudov/powergslb',
      description='PowerDNS Remote GSLB Backend',
      long_description='PowerGSLB is a simple DNS Global Server Load Balancing (GSLB) solution',
      packages=['powergslb'],
      package_dir={'': 'src'},
      platforms=['Linux'],
      license='GPLv2'
      )
