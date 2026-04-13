from abc import ABC, abstractmethod

class AbstractAIClient(ABC):
    @abstractmethod
    def __init__(self, api_key=None):
        pass

    @abstractmethod
    def send_message(self, text):
        pass

    @abstractmethod
    def run_analysis(self, abap_code, attributes, mode):
        pass
