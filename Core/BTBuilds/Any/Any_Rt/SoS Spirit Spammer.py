"""BT port of Builds/Any/Any_Rt/SoS Spirit Spammer.py.

Same rotation as the Ritualist-primary variant, matched on secondary Ritualist
for any primary. Legacy duplicated the whole ladder; here it is shared.
"""

from Core import BldMgrBT, Profession

from ...Ritualist.Rt_Any.sos_rotation import (
    SOS_OPTIONAL_SKILLS,
    SOS_REQUIRED_SKILLS,
    SOS_TEMPLATE_CODE,
    SoSRotationMixin,
)


class SoS_Spirit_Spammer_AnyRt(SoSRotationMixin, BldMgrBT):
    def __init__(self, match_only: bool = False):
        super().__init__(
            name="SoS Spirit Spammer",
            required_primary=Profession(0),
            required_secondary=Profession.Ritualist,
            template_code=SOS_TEMPLATE_CODE,
            required_skills=list(SOS_REQUIRED_SKILLS),
            optional_skills=list(SOS_OPTIONAL_SKILLS),
        )
        if match_only:
            return
        self.configure_rotation()
