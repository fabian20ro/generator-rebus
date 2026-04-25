import re

with open("tests/generator/cli/test_run_all.py", "r") as f:
    content = f.read()

content = content.replace(
    "build_job = lambda item: _StaticJob(item)",
    "def build_job(item): return _StaticJob(item)"
)

with open("tests/generator/cli/test_run_all.py", "w") as f:
    f.write(content)
