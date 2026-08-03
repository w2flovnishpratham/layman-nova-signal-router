#!/usr/bin/env python
from __future__ import annotations

import time

from app.workers.pine_conversion_worker import process_queued_conversions_once, recover_stale_requests

recover_stale_requests()
while True:
    if not process_queued_conversions_once(limit=4):
        time.sleep(.2)
