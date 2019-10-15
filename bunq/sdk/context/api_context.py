from __future__ import annotations

import datetime
import typing
from typing import List, Optional

from Cryptodome.PublicKey import RSA

from bunq.sdk.context.api_environment_type import ApiEnvironmentType
from bunq.sdk.context.installation_context import InstallationContext
from bunq.sdk.context.session_context import SessionContext
from bunq.sdk.exception.bunq_exception import BunqException
from bunq.sdk.json import converter
from bunq.sdk.model.generated import endpoint
from bunq.sdk.security import security

if typing.TYPE_CHECKING:
    from bunq.sdk.model.core.session_server import SessionServer


class ApiContext:
    """
    :type _environment_type: ApiEnvironmentType
    :type _api_key: str
    :type _session_context: SessionContext
    :type _installation_context: InstallationContext
    :type _proxy_url: str|None
    """

    # File mode for saving and restoring the context
    _FILE_MODE_WRITE = 'w'
    _FILE_MODE_READ = 'r'

    # Minimum time to session expiry not requiring session reset
    _TIME_TO_SESSION_EXPIRY_MINIMUM_SECONDS = 30

    # Dummy ID to pass to Session endpoint
    _SESSION_ID_DUMMY = 0

    # Default path to the file storing serialized API context
    _PATH_API_CONTEXT_DEFAULT = 'bunq.conf'

    def __init__(self,
                 environment_type: ApiEnvironmentType,
                 api_key: str,
                 device_description: str,
                 permitted_ips: List[str] = None,
                 proxy_url: List[str] = None) -> None:
        """

        :param environment_type:
        :param api_key:
        :param device_description:
        :param permitted_ips:
        :param proxy_url:
        """

        if permitted_ips is None:
            permitted_ips = []

        self._environment_type = environment_type
        self._api_key = api_key
        self._installation_context = None
        self._session_context = None
        self._proxy_url = proxy_url
        self._initialize(device_description, permitted_ips)

    def _initialize(self,
                    device_description: str,
                    permitted_ips: List[str]) -> None:
        """

        :param device_description:
        :param permitted_ips:
        """

        self._initialize_installation()
        self._register_device(device_description, permitted_ips)
        self._initialize_session()

    def _initialize_installation(self) -> None:
        from bunq.sdk.model.core.installation import Installation

        private_key_client = security.generate_rsa_private_key()

        installation = Installation.create(
            self,
            security.public_key_to_string(private_key_client.publickey())
        ).value
        token = installation.token.token
        public_key_server_string = \
            installation.server_public_key.server_public_key
        public_key_server = RSA.import_key(public_key_server_string)

        self._installation_context = InstallationContext(
            token,
            private_key_client,
            public_key_server
        )

    def _register_device(self,
                         device_description: str,
                         permitted_ips: List[str]) -> None:
        """

        :param device_description:
        :param permitted_ips:
        """

        from bunq.sdk.model.core.device_server_internal import DeviceServerInternal

        DeviceServerInternal.create(
            device_description,
            self.api_key,
            permitted_ips,
            api_context=self
        )

    def _initialize_session(self) -> None:
        from bunq.sdk.model.core.session_server import SessionServer

        session_server = SessionServer.create(self).value
        token = session_server.token.token
        expiry_time = self._get_expiry_timestamp(session_server)
        user_id = session_server.get_referenced_user().id_

        self._session_context = SessionContext(token, expiry_time, user_id)

    @classmethod
    def _get_expiry_timestamp(cls,
                              session_server: SessionServer) -> datetime.datetime:
        """

        :param session_server:
        """

        timeout_seconds = cls._get_session_timeout_seconds(session_server)
        time_now = datetime.datetime.now()

        return time_now + datetime.timedelta(seconds=timeout_seconds)

    @classmethod
    def _get_session_timeout_seconds(cls, session_server: SessionServer) -> int:
        """

        :param session_server:
        """

        if session_server.user_company is not None:
            return session_server.user_company.session_timeout
        elif session_server.user_person is not None:
            return session_server.user_person.session_timeout
        elif session_server.user_api_key is not None:
            return session_server \
                .user_api_key \
                .requested_by_user \
                .get_referenced_object() \
                .session_timeout
        else:
            raise BunqException()

    def ensure_session_active(self) -> bool:
        """
        Resets the session if it has expired.

        """

        if not self.is_session_active():
            self.reset_session()

            return True

        return False

    def is_session_active(self) -> bool:
        if self.session_context is None:
            return False

        time_now = datetime.datetime.now()
        time_to_expiry = self.session_context.expiry_time - time_now
        time_to_expiry_minimum = datetime.timedelta(
            seconds=self._TIME_TO_SESSION_EXPIRY_MINIMUM_SECONDS
        )

        return time_to_expiry > time_to_expiry_minimum

    def reset_session(self) -> None:
        """
        Closes the current session and opens a new one.

        """

        self._drop_session_context()
        self._initialize_session()

    def _drop_session_context(self) -> None:
        self._session_context = None

    def close_session(self) -> None:
        """
        Closes the current session.

        """

        self._delete_session()
        self._drop_session_context()

    def _delete_session(self) -> None:
        endpoint.Session.delete(self._SESSION_ID_DUMMY)

    @property
    def environment_type(self) -> ApiEnvironmentType:
        return self._environment_type

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def token(self) -> Optional[str]:
        if self._session_context is not None:
            return self.session_context.token
        elif self._installation_context is not None:
            return self.installation_context.token
        else:
            return None

    @property
    def installation_context(self) -> InstallationContext:
        return self._installation_context

    @property
    def session_context(self) -> SessionContext:
        return self._session_context

    @property
    def proxy_url(self) -> str:
        return self._proxy_url

    def save(self, path: str = None) -> None:
        """

        :param path:
        """

        if path is None:
            path = self._PATH_API_CONTEXT_DEFAULT

        with open(path, self._FILE_MODE_WRITE) as file_:
            file_.write(self.to_json())

    def to_json(self) -> str:
        """
        Serializes an ApiContext to JSON string.

        """

        return converter.class_to_json(self)

    @classmethod
    def restore(cls, path: str = None) -> 'ApiContext':
        """

        :param path:
        """

        if path is None:
            path = cls._PATH_API_CONTEXT_DEFAULT

        with open(path, cls._FILE_MODE_READ) as file_:
            return cls.from_json(file_.read())

    @classmethod
    def from_json(cls, json_str: str) -> 'ApiContext':
        """
        Creates an ApiContext instance from JSON string.

        :type json_str:
        """

        return converter.json_to_class(ApiContext, json_str)

    def __eq__(self, other: 'ApiContext') -> bool:
        """

        :param other:
        """

        return (self.token == other.token and
                self.api_key == other.api_key and
                self.environment_type == other.environment_type)
