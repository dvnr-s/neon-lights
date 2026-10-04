"""Hardware layer: BLE transport and the Magic Lantern wire protocol.

Nothing in this package imports Qt or knows about effects/UI.
"""

from .protocol import MagicLanternProtocol, DeviceProfile, PROFILES, profile_for_name
from .controller import BleController, ConnectionState, ConnectionStatus

__all__ = [
    "MagicLanternProtocol",
    "DeviceProfile",
    "PROFILES",
    "profile_for_name",
    "BleController",
    "ConnectionState",
    "ConnectionStatus",
]
