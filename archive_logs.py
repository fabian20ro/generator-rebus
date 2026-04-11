import re

log_file = "ITERATION_LOG.md"
archive_file = "ITERATION_LOG_ARCHIVE.md"

with open(log_file, "r", encoding="utf-8") as f:
    content = f.read()

# Header ends here
marker = "<!-- new entries above this line, most recent first -->"
parts = content.split(marker)
header = parts[0].strip() + "\n\n" + marker + "\n\n---\n"
body = parts[1]

# Split body by separator
# The separator is exactly \n\n---\n\n according to my tail/grep
blocks = re.split(r"\n---\n", body)

entries = []
for b in blocks:
    if "### [" in b:
        entries.append(b.strip())

print(f"Total blocks with ### [: {len(entries)}")

march = []
april = []

for e in entries:
    m = re.search(r"### \[2026-(\d{2})-\d{2}\]", e)
    if m:
        month = m.group(1)
        if month == "03":
            march.append(e)
        else:
            april.append(e)
    else:
        april.append(e)

print(f"March entries: {len(march)}")
print(f"April entries: {len(april)}")

if len(march) > 0:
    # Write updated ITERATION_LOG.md
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.write("\n\n---\n\n".join(april))
        f.write("\n\n---\n")

    # Write ITERATION_LOG_ARCHIVE.md
    archive_header = "# Iteration Log Archive\n\n> append-only. older entries from ITERATION_LOG.md.\n\n"
    with open(archive_file, "w", encoding="utf-8") as f:
        f.write(archive_header)
        f.write("\n\n---\n\n".join(march))
        f.write("\n\n---\n")
else:
    print("No March entries found to archive.")
