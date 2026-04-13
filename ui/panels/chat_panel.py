import customtkinter as ctk
import re
import threading

class ChatPanel(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, width=380, corner_radius=0)
        self.app = app_context
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        self._setup_ui()

    def _setup_ui(self):
        ctk.CTkLabel(self, text="🤖 AI ASSISTANT", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, column=0, padx=10, pady=20, sticky="w")
        
        self.chat_log = ctk.CTkTextbox(self, font=ctk.CTkFont(size=13), wrap="word", fg_color="#2b2b2b")
        self.chat_log.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.chat_log.configure(state="disabled")
        self.app.chat_log = self.chat_log

        input_frame = ctk.CTkFrame(self, fg_color="transparent")
        input_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 15))
        input_frame.grid_columnconfigure(0, weight=1)
        
        self.chat_input = ctk.CTkEntry(input_frame, placeholder_text="Ask about the code...", height=45)
        self.chat_input.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.chat_input.bind("<Return>", lambda e: self.app.send_chat())
        self.app.chat_input = self.chat_input

        self.send_btn = ctk.CTkButton(input_frame, text="Send", width=80, height=45, command=self.app.send_chat)
        self.send_btn.grid(row=0, column=1)
        self.send_btn.configure(state="disabled")
        self.app.send_btn = self.send_btn

    def on_chat_response(self, text):
        # Autonomous FETCH detection
        fetch_pattern = r"\[\[FETCH:(.*?):(.*?)\]\]"
        matches = re.findall(fetch_pattern, text)
        for cat, name in matches:
            self.app.write_log(f"AI requested fetch: {cat}:{name}")
            conn = self.app.get_current_conn()
            threading.Thread(target=self.app.run_sub_fetch, args=(conn, name.strip().upper(), cat.strip().upper()), daemon=True).start()

        # Proposal detection
        prop_pattern = r"\[\[PROPOSAL:(.*?)\]\](.*?)\[\[END_PROPOSAL\]\]"
        match = re.search(prop_pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            clean = re.sub(prop_pattern, "--- [AI Suggestion in Tab] ---", text, flags=re.DOTALL)
            self.app.update_chat_log(f"Assistant: {clean}")
            fname, scode = match.group(1).strip(), match.group(2).strip()
            self.app.open_suggestion_tab(fname, scode)
        else:
            self.app.update_chat_log(f"Assistant: {text}")
        
        self.send_btn.configure(state="normal")
