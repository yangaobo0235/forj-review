"""Bounded configuration events; exception values never enter persistent logs."""

import copy
import logging
import re
import traceback
from pathlib import Path


class ConfigurationLogFormatter(logging.Formatter):
    def format(self, record):
        item = copy.copy(record)
        text = item.getMessage()
        text = re.sub(r"(?:https?|postgresql)://[^\s]+", "[url-redacted]", text)
        text = re.sub(r"(?i)(bearer\s+|(?:password|token|api_key)=)[^\s,;]+", r"\1[redacted]", text)
        item.msg = text.replace("\r", "\\r").replace("\n", "\\n")[:8000]
        item.args, item.exc_text, item.stack_info = (), None, None
        return super().format(item).replace("\n", "\\n").replace("\r", "\\r")

    def formatException(self, exc_info):
        locations = " > ".join(
            f"{Path(f.filename).name}:{f.lineno}:{f.name}"
            for f in traceback.extract_tb(exc_info[2])[-12:]
        )
        return f"exception_type={exc_info[0].__name__} stack={locations}"
