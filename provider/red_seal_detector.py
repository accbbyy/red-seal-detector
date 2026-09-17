from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from typing import Any


class RedSealDetectorProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            """无需凭据"""
        except Exception as e:
            raise ToolProviderCredentialValidationError(str(e))
