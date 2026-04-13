import customtkinter as ctk
from tkinter import ttk
from utils.highlighter import ABAPHighlighter

class EditorPanel(ctk.CTkFrame):
    def __init__(self, parent, app_context):
        super().__init__(parent, corner_radius=0, fg_color="transparent")
        self.app = app_context
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        
        self.tabs_dict = {}
        self.active_tab_name = None
        self._setup_ui()

    def _setup_ui(self):
        # Object Fetch Header
        self.fetch_bar = ctk.CTkFrame(self, height=60)
        self.fetch_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        self.fetch_bar.grid_columnconfigure(1, weight=1)

        self.type_menu = ctk.CTkOptionMenu(self.fetch_bar, values=["Program", "Table", "Structure", "Function Module", "Global Class", "Function Group"], width=130, height=40)
        self.type_menu.grid(row=0, column=0, padx=10)
        self.app.fetch_type_var = self.type_menu._variable # Link to app context

        self.name_entry = ctk.CTkEntry(self.fetch_bar, placeholder_text="Object Name...", height=40)
        self.name_entry.grid(row=0, column=1, sticky="ew", padx=5)
        self.app.prog_name = self.name_entry

        self.fetch_btn = ctk.CTkButton(self.fetch_bar, text="Fetch", width=100, height=40, command=self.app.fetch_program_flow)
        self.fetch_btn.grid(row=0, column=2, padx=10)
        self.app.fetch_btn = self.fetch_btn

        # Custom Tab Bar
        self.headers_bar = ctk.CTkFrame(self, height=35, fg_color="#1E1E1E")
        self.headers_bar.grid(row=1, column=0, sticky="ew", padx=10)
        self.headers_bar.grid_propagate(False)

        self.content_area = ctk.CTkFrame(self, corner_radius=10, fg_color="#1a1a1b")
        self.content_area.grid(row=2, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)

    def add_tab(self, name, is_closable=True):
        header = ctk.CTkFrame(self.headers_bar, fg_color="#2d2d2d", height=35, corner_radius=5)
        header.pack(side="left", padx=2, pady=2)
        
        lbl = ctk.CTkLabel(header, text=name, font=ctk.CTkFont(size=12))
        lbl.pack(side="left", padx=(10, 5))
        lbl.bind("<Button-1>", lambda e: self.set_active(name))
        
        if is_closable:
            ctk.CTkButton(header, text="X", width=20, height=20, fg_color="transparent", hover_color="#8b3a36", 
                         command=lambda: self.close_tab(name)).pack(side="left", padx=(0, 5))
            
        content = ctk.CTkFrame(self.content_area, fg_color="transparent")
        self.tabs_dict[name] = {"header": header, "content": content}
        return content

    def set_active(self, name):
        if self.active_tab_name and self.active_tab_name in self.tabs_dict:
            self.tabs_dict[self.active_tab_name]["header"].configure(fg_color="#2d2d2d")
            self.tabs_dict[self.active_tab_name]["content"].grid_forget()
        
        self.active_tab_name = name
        self.tabs_dict[name]["header"].configure(fg_color="#347083")
        self.tabs_dict[name]["content"].grid(row=0, column=0, sticky="nsew")
        self.app.active_tab_name = name

    def close_tab(self, name):
        if name not in self.tabs_dict: return
        self.tabs_dict[name]["header"].destroy()
        self.tabs_dict[name]["content"].destroy()
        del self.tabs_dict[name]
        if self.app.tabs_dict and name in self.app.tabs_dict: del self.app.tabs_dict[name]
        
        if self.active_tab_name == name:
            self.active_tab_name = None
            if self.tabs_dict:
                self.set_active(list(self.tabs_dict.keys())[-1])
            else:
                self.app.write_log("All tabs closed.")
