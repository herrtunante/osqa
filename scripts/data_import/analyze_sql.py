#!/usr/bin/env python
import re

with open('importData/osqa-20-10-2025_23-30-01.sql', encoding='utf-8', errors='ignore') as f:
    content = f.read()
    tables = set(re.findall(r'INSERT INTO `([^`]+)`', content))
    print('Tables with data:')
    for table in sorted(tables):
        count = len(re.findall(rf'INSERT INTO `{re.escape(table)}`', content))
        print(f'  {table}: {count} INSERT statements')
