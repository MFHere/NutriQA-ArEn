import os,sys
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)),'services'))
from typing import Optional,List
from pydantic import BaseModel, field_validator, StrictBool, model_validator, Field
from agent_component import QAItem
import re

YOUTUBE_REGEX = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$")




class RequestSchema(BaseModel):
    url: str
    qa_option: StrictBool
    stt_option: StrictBool
    llm_name: Optional[str] = None
    stt_name: Optional[str] = None
    stt_api_key: Optional[str] = None
    llm_api_key: Optional[str] = None

    @field_validator('url')
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        if not YOUTUBE_REGEX.match(v):
            raise ValueError("URL must be a valid YouTube link")
        return v

    @model_validator(mode="after")
    def check_api_keys(cls, values):
        if values.qa_option:
            if not values.llm_api_key:
                raise ValueError("llm_api_key must be provided if qa_option is True")
            if not values.llm_name:
                raise ValueError("llm_name must be provided if qa_option is True")

        if values.stt_option:
            if not values.stt_api_key:
                raise ValueError("stt_api_key must be provided if stt_option is True")
            if not values.stt_name:
                raise ValueError("stt_name must be provided if stt_option is True")

        return values


class ResponseSchema(BaseModel):
    text: Optional[str] = None
    QA: List[QAItem] = Field(default_factory=list)  # <- fix here           
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    
