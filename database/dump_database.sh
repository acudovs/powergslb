#!/bin/bash

mysqldump -u powergslb -pyour-database-password-here -h 127.0.0.1 powergslb --no-data --skip-comments --compact --no-set-names --skip-set-charset | grep -v -E '^\/\*' | sed -e 's/AUTO_INCREMENT=[0-9]*//' > scheme.sql

mysqldump -u powergslb -pyour-database-password-here -h 127.0.0.1 powergslb --no-create-info --skip-comments --compact --no-set-names --skip-set-charset --extended-insert=FALSE > data.sql

