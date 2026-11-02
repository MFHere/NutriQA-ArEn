from pydantic import BaseModel
from typing import List

class QAItem(BaseModel):
    question: str
    answer: str

class QAListResponse(BaseModel):
    items: List[QAItem]


class QAEvaluation(BaseModel):
    acceptable: bool
    reason: str
