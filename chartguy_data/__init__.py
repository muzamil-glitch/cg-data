"""chartguy_data: a careful layer over the official lse-data client.

    from chartguy_data import CGData
    cg = CGData()                       # reads LSE_API_KEY
    print(cg.nfp_study("USD/JPY", years=5).report())
"""
from .core import CGData, CGDataError, Result
from .helpers import api_date, end_exclusive, first_friday_nfp, pip_size, to_utc

__all__ = ["CGData", "CGDataError", "Result", "api_date", "end_exclusive",
           "first_friday_nfp", "pip_size", "to_utc"]
__version__ = "0.1.0"
