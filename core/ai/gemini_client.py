from google import genai
from google.genai import types
import os
from core.ai.base import AbstractAIClient
from core.config import Config

class GeminiClient(AbstractAIClient):
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("Gemini API Key is missing.")
        
        self.client = genai.Client(api_key=self.api_key)
        self.model_id = 'gemini-3-flash-preview'
        self.system_instruction = (
            "You are an expert SAP ABAP AI Co-pilot integrated into a modern IDE. "
            "Your goal is to help developers analyze, debug, and optimize ABAP code. "
            "\nMULTI-FILE AUTONOMY: "
            "You have access to the Object Explorer. You can autonomously fetch any object "
            "using the tag: [[FETCH:Category:Name]] "
            "Example: [[FETCH:DICT:BSID]], [[FETCH:INCLUDE:ZMAIN_TOP]], [[FETCH:CLASS:ZCL_HELPER]]. "
            "When you see a reference in the code that you don't understand, FETCH IT immediately. "
            "\nCODE IMPROVEMENTS: "
            "Wrap suggestions with [[PROPOSAL:FileName]]CODE[[END_PROPOSAL]]. "
            "\nPROACTIVE ROLE: "
            "Be professional and proactive. If you need more context to be accurate, use the FETCH command."
        )
        self.chat_session = None

    def _ensure_chat(self):
        if not self.chat_session:
            self.chat_session = self.client.chats.create(
                model=self.model_id,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_instruction
                )
            )

    def send_message(self, text):
        self._ensure_chat()
        try:
            response = self.chat_session.send_message(text)
            return response.text
        except Exception as e:
            return f"Error communicating with Gemini (GenAI): {e}"

    def run_analysis(self, abap_code, attributes, mode):
        prompt = Config.get_prompt(mode, abap_code, attributes)
        return self.send_message(prompt)
