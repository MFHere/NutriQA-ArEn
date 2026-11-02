import os,sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
import tiktoken
from openai import OpenAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langchain_community.callbacks.manager import get_openai_callback
from models import ResponseSchema


from .agent_component import system_question_prompt,QAListResponse



class QAProcessingAgent():

    def __init__(self,llm_apikey:str,llm_name:str):
        self.llm_api_key=llm_apikey
        self.llm_name=llm_name
        self.llm=ChatGroq(model=self.llm_name,
                            temperature=0,
                            api_key=self.llm_api_key,
                            timeout=None,
                            max_retries=2) if self.llm_name =="llama-3.3-70b-versatile" else ChatOpenAI(model=self.llm_name,
                                                                                                        temperature=0,
                                                                                                        max_tokens=None,
                                                                                                        timeout=None,
                                                                                                        max_retries=2,
                                                                                                        api_key=self.llm_api_key)
        
        self.max_tokens= 130000 if self.llm_name =="llama-3.3-70b-versatile" else 390000

        self.input_cost={'llama-3.3-70b-versatile':1/1700000,
                         'gpt-5-nano':0.05/1000000,
                         'gpt-4o-mini':0.15/1000000}
        
        self.output_cost={'llama-3.3-70b-versatile':1/1300000,
                        'gpt-5-nano':0.40/1000000,
                        'gpt-4o-mini':0.60/1000000}
        

    

    def QA_generator(self, context, max_questions: int | None = None) -> ResponseSchema:
        QA_llm_structure = self.llm.with_structured_output(QAListResponse)
        chunks = self.count_and_chunk_tokens(context)

        results = ResponseSchema()  # initialize the Pydantic model

        for chunk in chunks:
            if max_questions is not None and len(results.QA) >= max_questions:
                break
            prefix = ""
            if max_questions is not None:
                remaining = max_questions - len(results.QA)
                prefix = f"Extract at most {remaining} question-answer pair(s) from the following context.\n\n"
            human_message = HumanMessage(content=prefix + chunk)
            with get_openai_callback() as cb:
                llm_results = QA_llm_structure.invoke([system_question_prompt, human_message])

                results.QA.extend(llm_results.items)
                if max_questions is not None:
                    results.QA = results.QA[:max_questions]

                results.total_input_tokens += cb.prompt_tokens
                results.total_output_tokens += cb.completion_tokens
                results.input_cost += cb.prompt_tokens * self.input_cost.get(self.llm_name, 0)
                results.output_cost += cb.completion_tokens * self.output_cost.get(self.llm_name, 0)
                results.total_cost = results.input_cost + results.output_cost

        return results



            
    def count_and_chunk_tokens(self,text):
        
        encoding = tiktoken.encoding_for_model("gpt-4o-mini" if self.llm_name=='llama-3.3-70b-versatile' else self.llm_name)
        tokens = encoding.encode(text)

        if len(tokens) < self.max_tokens:
            return [text]  # return as single chunk

        chunks = []
        for i in range(0, len(tokens), self.max_tokens):
            chunk_tokens = tokens[i:i + self.max_tokens]
            chunk_text = encoding.decode(chunk_tokens)
            chunks.append(chunk_text)

        return chunks