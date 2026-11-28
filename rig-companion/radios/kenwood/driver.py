"""Kenwood radio package: TH-D75, TH-D74, TM-D750 over Bluetooth SPP or USB.

The Kenwood CAT + KISS worker is the reference driver the app was built on, so
its code lives in rigzero.radio. This package registers it and its radios.
"""
from rigzero.radio import Radio


class Driver(Radio):
    family = "kenwood"

    def __init__(self, store, link=None, model="TH-D75", package=None):
        self.model_key = model
        self.package = package or {}
        self.usb_baud = self.package.get("usb_baud", 9600)
        super().__init__(store, link)
