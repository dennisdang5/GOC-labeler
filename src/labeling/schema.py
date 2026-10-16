"""Label schema (pydantic). GOC is judged directly against the operational definition in
CLAUDE.md: yes / no / unsure (stored as "unclear"). No evidence, reason or confidence fields
(decided 2026-10-16). Label files contain no note text.
"""

from typing import Literal

from pydantic import BaseModel, Field


class GOCLabel(BaseModel):
    row_id: int
    assignment: str
    goc_present: Literal["yes", "no", "unclear"]  # "unclear" is shown to labelers as "unsure"
    annotator: str = Field(pattern=r"^[A-Za-z]{1,5}$")
    labeled_at: str
