import re

with open("tests/generator/workflows/test_generate_define.py", "r") as f:
    content = f.read()

content = content.replace("dex=dex,", "dex=dex,\n            clue_canon=MagicMock(),")

with open("tests/generator/workflows/test_generate_define.py", "w") as f:
    f.write(content)
