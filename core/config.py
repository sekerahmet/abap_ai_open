import os
from utils.env_loader import load_robust_env

# Load environment variables robustly
load_robust_env()

class Config:
    # SAP Connection Parameters
    SAP_CONNECTION = {
        "ashost": os.getenv("SAP_ASHOST"),
        "sysnr": os.getenv("SAP_SYSNR", "00"),
        "client": os.getenv("SAP_CLIENT", "100"),
        "user": os.getenv("SAP_USER"),
        "passwd": os.getenv("SAP_PASSWD")
    }

    # Anthropic API Parameter
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

    # Prompt Templates for different modes
    PROMPTS = {
        "review": "You are an expert SAP ABAP developer. Review the following ABAP code and provide a general code review, identifying any bugs, syntax issues, or logical flaws. \n\nCode:\n{code}",
        "performance": "You are an expert SAP ABAP performance tuner. Analyze the following ABAP code specifically for performance bottlenecks. Look for inefficient database queries (like nested SELECTs inside LOOPs), missing WHERE clauses, or sub-optimal internal table operations. Suggest optimizations.\n\nCode:\n{code}",
        "security": "You are an expert SAP ABAP security auditor. Analyze the following ABAP code for security vulnerabilities. Check for missing AUTHORITY-CHECK statements, SQL injection risks, directory traversal, and insecure data handling. Provide a detailed risk report.\n\nCode:\n{code}",
        "documentation": "You are an expert technical writer for SAP systems. Read the following ABAP code and generate a comprehensive technical documentation. Include the purpose of the program, inputs, outputs, and a high-level explanation of its logic.\n\nCode:\n{code}"
    }

    @classmethod
    def get_prompt(cls, mode, abap_code, attributes=None):
        template = cls.PROMPTS.get(mode, cls.PROMPTS["review"])
        prompt = template.format(code=abap_code)
        
        if attributes:
             # Prepend attributes for context
             attr_str = "\n".join([f"{k}: {v}" for k, v in attributes.items() if v])
             context = f"--- PROGRAM ATTRIBUTES ---\n{attr_str}\n--------------------------\n\n"
             prompt = context + prompt
             
        return prompt
