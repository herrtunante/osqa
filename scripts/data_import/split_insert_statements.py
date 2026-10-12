#!/usr/bin/env python
"""
Split multi-row INSERT statements into single-row INSERTs
This makes the MySQL dump easier to import into SQLite
"""
import re

print("Splitting multi-row INSERT statements...")

with open('importData/osqa-20-10-2025_23-30-01.sql', 'r', encoding='utf-8', errors='ignore') as f:
    content = f.read()

# Find all INSERT statements
insert_pattern = r"(INSERT INTO `([^`]+)` VALUES\s*)(\([^;]+\));"

def split_values(values_str):
    """Split (val1, val2),(val3,val4) into [(val1, val2), (val3,val4)]"""
    results = []
    current = ''
    paren_depth = 0
    in_string = False
    escape_next = False

    for i, char in enumerate(values_str):
        if escape_next:
            current += char
            escape_next = False
            continue

        if char == '\\':
            current += char
            escape_next = True
            continue

        if char == "'" and not escape_next:
            in_string = not in_string
            current += char
            continue

        if in_string:
            current += char
            continue

        if char == '(':
            if paren_depth == 0:
                current = '('
            else:
                current += char
            paren_depth += 1
        elif char == ')':
            current += char
            paren_depth -= 1
            if paren_depth == 0:
                results.append(current)
                current = ''
        elif char == ',' and paren_depth == 0:
            # Skip commas between value sets
            continue
        else:
            current += char

    return results

# Process the file
output_lines = []
line_num = 0

for line in content.split('\n'):
    line_num += 1
    if line.startswith('INSERT INTO'):
        match = re.match(r"INSERT INTO `([^`]+)` VALUES\s*(.+);", line)
        if match:
            table_name = match.group(1)
            values = match.group(2)

            # Split into individual rows
            rows = split_values(values)

            if len(rows) > 1:
                # Multi-row INSERT - split it
                for row in rows:
                    output_lines.append(f"INSERT INTO `{table_name}` VALUES {row};")
            else:
                # Single row - keep as is
                output_lines.append(line)
        else:
            output_lines.append(line)
    else:
        output_lines.append(line)

    if line_num % 100 == 0:
        print(f"  Processed {line_num} lines...", end='\r')

print(f"\n  ✓ Processed {line_num} lines total")

# Write output
print("  Writing split SQL file...")
with open('importData/osqa-split.sql', 'w', encoding='utf-8') as f:
    f.write('\n'.join(output_lines))

print(f"  ✓ Created importData/osqa-split.sql")
