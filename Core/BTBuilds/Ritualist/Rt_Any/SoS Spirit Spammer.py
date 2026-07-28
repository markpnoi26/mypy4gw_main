"""BT port of Builds/Ritualist/Rt_Any/SoS Spirit Spammer.py.

Rotation lives in sos_rotation.py — shared with the Any/Any_Rt variant, which
legacy kept as a full duplicate.
"""

from Core import BldMgrBT
from Core import Profession

from .sos_rotation import SOS_OPTIONAL_SKILLS
from .sos_rotation import SOS_REQUIRED_SKILLS
from .sos_rotation import SOS_TEMPLATE_CODE
from .sos_rotation import SoSRotationMixin


class SoS_Spirit_Spammer(SoSRotationMixin, BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="SoS Spirit Spammer",
            required_primary=Profession.Ritualist,
            template_code=SOS_TEMPLATE_CODE,
            required_skills=list(SOS_REQUIRED_SKILLS),
            optional_skills=list(SOS_OPTIONAL_SKILLS),
        )
        if match_only:
            return
        self.configure_rotation()
