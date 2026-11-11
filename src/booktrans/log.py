#!/usr/bin/env python3
"""Tiny progress logger shared by the stages (stderr, flushed, timestamped).

``info`` always prints; ``debug`` only when ``BOOKTRANS_VERBOSE=1`` (the wrapper's
``-v``).  Each line shows seconds since the process started, so slow steps stand out.
"""
import os
import sys
import time

# The wrapper exports its start time so all stages share one clock.
_T0 = float(os.environ.get('BOOKTRANS_T0') or time.time())


def verbose():
    return os.environ.get('BOOKTRANS_VERBOSE') == '1'


def _out(tag, msg):
    sys.stderr.write('[%6.1fs] %-7s %s\n' % (time.time() - _T0, tag, msg))
    sys.stderr.flush()


def info(tag, msg):
    _out(tag, msg)


def debug(tag, msg):
    if verbose():
        _out(tag, '  ' + msg)
