# ruff: noqa
"""Semgrep rule tests for tools/ci/semgrep.yml. Never imported or run.

CI: semgrep --test --config tools/ci/semgrep.yml tools/ci/semgrep.py
Each case is preceded by a Semgrep test annotation naming the rules that must (or must not)
match the next line. Do not write the annotation keywords anywhere else in this file.
"""

import asyncio
import builtins
import hashlib
import os
import pickle
import sqlite3
import subprocess

import yaml


def cases(path, data, url, client):
    # ruleid: no-dynamic-exec
    eval(data)
    # ruleid: no-dynamic-exec
    exec(data)

    # ruleid: subprocess-only-in-launcher
    subprocess.run(["tool"])
    # ruleid: subprocess-only-in-launcher
    os.system("tool")
    # ruleid: no-shell-true, subprocess-only-in-launcher
    subprocess.run("tool", shell=True)

    # ruleid: sqlite-only-in-storage
    sqlite3.connect(path)

    # ruleid: no-assert-for-checks
    assert data

    # ruleid: no-swallow
    try:
        os.remove(path)
    except OSError:
        pass

    # ruleid: no-swallow
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        data = None

    # ruleid: no-swallow
    try:
        os.remove(path)
    except OSError as error:
        pass

    # ok: no-swallow
    try:
        os.remove(path)
    # ok: broad-except-only-in-allowed
    except OSError:
        data = None

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except Exception:
        data = None

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except Exception as error:
        data = error

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except BaseException as error:
        data = error

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except:
        data = None

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except (Exception, asyncio.CancelledError) as error:
        data = error

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except (OSError, BaseException):
        data = None

    try:
        os.remove(path)
    except FileNotFoundError:
        data = None
    # ruleid: broad-except-only-in-allowed
    except (OSError, builtins.Exception):
        data = None

    try:
        os.remove(path)
    # ruleid: broad-except-only-in-allowed
    except (
        OSError,
        Exception,
    ):
        data = None

    try:
        os.remove(path)
    # ok: broad-except-only-in-allowed
    except (OSError, ExceptionGroup, BaseExceptionGroup) as error:
        data = error

    try:
        os.remove(path)
    # ok: broad-except-only-in-allowed
    except CustomException:
        data = None

    # ok: broad-except-only-in-allowed
    except_count = 1

    # ruleid: no-unsafe-deserialize
    pickle.loads(data)
    # ruleid: no-unsafe-deserialize
    yaml.load(data)
    # ok: no-unsafe-deserialize
    yaml.safe_load(data)

    # ruleid: no-tls-bypass
    client.get(url, verify=False)

    # ruleid: no-weak-hash
    hashlib.md5(data)
    # ruleid: no-weak-hash
    hashlib.sha1(data)
    # ok: no-weak-hash
    hashlib.sha256(data)
    return data
