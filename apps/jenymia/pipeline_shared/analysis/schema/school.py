"""What the analysis returns for Schule & Alltag (school_everyday), on top of
the shared part: the child certification and the safety texts it shares
with toys. Everyday facts - cleaning, spare parts, weight - are specs."""

from .shared import CHILD_CERTIFIED, BaseAnalysis, Flag, SafetyText, Translated


class SchoolText(SafetyText):
    pass


class SchoolAnalysis(BaseAnalysis):
    is_child_certified: Flag = CHILD_CERTIFIED
    translations: Translated[SchoolText]
