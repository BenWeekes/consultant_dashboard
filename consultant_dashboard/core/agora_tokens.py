"""
Minimal Agora v007 RTC token generation for dashboard-issued crisis PSTN bundles.
"""

import base64
import hmac
import secrets
import struct
import time
import zlib
from collections import OrderedDict
from hashlib import sha256


def _pack_uint16(x):
    return struct.pack("<H", int(x))


def _pack_uint32(x):
    return struct.pack("<I", int(x))


def _pack_string(string):
    if isinstance(string, str):
        string = string.encode("utf-8")
    return _pack_uint16(len(string)) + string


def _pack_map_uint32(values):
    return _pack_uint16(len(values)) + b"".join(
        [_pack_uint16(k) + _pack_uint32(v) for k, v in values.items()]
    )


class _Service:
    def __init__(self, service_type):
        self._type = service_type
        self._privileges = {}

    def add_privilege(self, privilege, expire):
        self._privileges[privilege] = expire

    def service_type(self):
        return self._type

    def pack(self):
        privileges = OrderedDict(sorted(self._privileges.items(), key=lambda item: int(item[0])))
        return _pack_uint16(self._type) + _pack_map_uint32(privileges)


class _ServiceRtc(_Service):
    kServiceType = 1
    kPrivilegeJoinChannel = 1
    kPrivilegePublishAudioStream = 2
    kPrivilegePublishVideoStream = 3
    kPrivilegePublishDataStream = 4

    def __init__(self, channel_name="", uid=0):
        super().__init__(_ServiceRtc.kServiceType)
        self._channel_name = channel_name.encode("utf-8")
        self._uid = b"" if uid == 0 else str(uid).encode("utf-8")

    def pack(self):
        return super().pack() + _pack_string(self._channel_name) + _pack_string(self._uid)


class _AccessToken:
    def __init__(self, app_id="", app_certificate="", issue_ts=0, expire=900):
        self._app_id = app_id
        self._app_cert = app_certificate
        self._issue_ts = issue_ts if issue_ts != 0 else int(time.time())
        self._expire = expire
        self._salt = secrets.SystemRandom().randint(1, 99999999)
        self._services = {}

    def add_service(self, service):
        self._services[service.service_type()] = service

    def _signing(self):
        signing = hmac.new(_pack_uint32(self._issue_ts), self._app_cert, sha256).digest()
        return hmac.new(_pack_uint32(self._salt), signing, sha256).digest()

    def _build_check(self):
        def is_uuid(data):
            if len(data) != 32:
                return False
            try:
                bytes.fromhex(data)
            except ValueError:
                return False
            return True

        return is_uuid(self._app_id) and is_uuid(self._app_cert) and bool(self._services)

    def build(self):
        if not self._build_check():
            return ""

        self._app_id = self._app_id.encode("utf-8")
        self._app_cert = self._app_cert.encode("utf-8")
        signing = self._signing()
        signing_info = (
            _pack_string(self._app_id)
            + _pack_uint32(self._issue_ts)
            + _pack_uint32(self._expire)
            + _pack_uint32(self._salt)
            + _pack_uint16(len(self._services))
        )
        for _, service in self._services.items():
            signing_info += service.pack()

        signature = hmac.new(signing, signing_info, sha256).digest()
        return "007" + base64.b64encode(
            zlib.compress(_pack_string(signature) + signing_info)
        ).decode("utf-8")


def build_rtc_token(app_id: str, app_certificate: str, channel_name: str, uid: str, privilege_expire: int = 3600):
    if not app_certificate:
        return app_id
    token = _AccessToken(app_id, app_certificate)
    rtc_service = _ServiceRtc(channel_name, uid)
    rtc_service.add_privilege(_ServiceRtc.kPrivilegeJoinChannel, privilege_expire)
    rtc_service.add_privilege(_ServiceRtc.kPrivilegePublishAudioStream, privilege_expire)
    rtc_service.add_privilege(_ServiceRtc.kPrivilegePublishVideoStream, privilege_expire)
    rtc_service.add_privilege(_ServiceRtc.kPrivilegePublishDataStream, privilege_expire)
    token.add_service(rtc_service)
    return token.build()
