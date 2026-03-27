import tkinter as tk


class SimpleLoadingIndicator:
    """Static loading indicator - no animation thread"""
    
    def __init__(self, parent, colors):
        self.frame = tk.Frame(parent, bg=colors['surface'],
                             highlightbackground=colors['accent'],
                             highlightthickness=2)
        
        self.label = tk.Label(self.frame, text="⏳ Loading...", 
                             font=('Segoe UI', 12),
                             bg=colors['surface'], fg=colors['text'])
        self.label.pack(padx=30, pady=20)
    
    def show(self, text=None):
        if text:
            self.label.config(text=f"⏳ {text}")
        self.frame.place(relx=0.5, rely=0.5, anchor="center")
        self.frame.lift()
    
    def hide(self):
        self.frame.place_forget()
    
    def update_text(self, text):
        self.label.config(text=f"⏳ {text}")


class EmptyState:
    """Simple empty state - no canvas animation"""
    
    def __init__(self, app):
        self.app = app
        self.icons = {
            'empty': '📷',
            'search': '🔍',
            'favorites': '💗',
            'trash': '🗑️',
            'error': '⚠️',
            'album': '📔',
            'tag': '🏷️',
            'video': '🎬',
            'duplicates': '🔍'
        }
    
    def show(self, parent, icon_key, title, subtitle, action_text=None, action_cmd=None):
        # Clear parent
        for w in parent.winfo_children():
            w.destroy()
        
        frame = tk.Frame(parent, bg=self.app.colors['bg'])
        frame.pack(expand=True)
        
        # Icon
        tk.Label(frame, text=self.icons.get(icon_key, '📷'), 
                font=('Segoe UI', 64),
                bg=self.app.colors['bg'], 
                fg=self.app.colors['accent']).pack(pady=20)
        
        # Title
        tk.Label(frame, text=title, font=self.app.font_title,
                bg=self.app.colors['bg'], 
                fg=self.app.colors['text']).pack()
        
        # Subtitle
        tk.Label(frame, text=subtitle, font=self.app.font_main,
                bg=self.app.colors['bg'],
                fg=self.app.colors['text_secondary']).pack(pady=10)
        
        # Action button
        if action_text and action_cmd:
            btn = tk.Button(frame, text=action_text, 
                           command=action_cmd,
                           font=self.app.font_bold,
                           bg=self.app.colors['accent'],
                           fg='white', relief='flat',
                           padx=20, pady=10)
            btn.pack(pady=20)


class KeyHintManager:
    """Simple keyboard hint display"""
    
    def __init__(self, app):
        self.app = app
        self.shortcuts = {}
        self.hint_window = None
    
    def register(self, key, desc, callback, ctrl=False, shift=False):
        binding = '<'
        if ctrl: 
            binding += 'Control-'
        if shift: 
            binding += 'Shift-'
        binding += f'{key}>'
        
        # Wrap with brief flash
        def wrapped(e):
            self._flash(key)
            return callback(e)
        
        self.app.root.bind(binding, wrapped)
        self.shortcuts[key] = {'desc': desc, 'ctrl': ctrl, 'shift': shift}
    
    def _flash(self, key):
        """Brief visual feedback"""
        flash = tk.Toplevel(self.app.root)
        flash.overrideredirect(True)
        flash.attributes('-topmost', True)
        
        label = tk.Label(flash, text=key.upper(), 
                        font=('Segoe UI', 12, 'bold'),
                        bg=self.app.colors['accent'], fg='white',
                        padx=10, pady=5)
        label.pack()
        
        # Position at bottom center
        x = self.app.root.winfo_x() + self.app.root.winfo_width()//2 - 20
        y = self.app.root.winfo_y() + self.app.root.winfo_height() - 80
        flash.geometry(f"+{x}+{y}")
        
        # Auto-remove
        flash.after(200, flash.destroy)
    
    def show_help(self):
        """Simple text dialog"""
        if self.hint_window and self.hint_window.winfo_exists():
            self.hint_window.destroy()
            return
        
        self.hint_window = tk.Toplevel(self.app.root)
        self.hint_window.title("Shortcuts")
        self.hint_window.geometry("300x400")
        
        text = tk.Text(self.hint_window, wrap=tk.WORD, padx=20, pady=20)
        text.pack(fill=tk.BOTH, expand=True)
        
        text.insert(tk.END, "KEYBOARD SHORTCUTS\n\n")
        
        for key, info in sorted(self.shortcuts.items()):
            mod = ""
            if info['ctrl']: 
                mod += "Ctrl+"
            if info['shift']: 
                mod += "Shift+"
            text.insert(tk.END, f"{mod}{key.upper()}: {info['desc']}\n")
        
        text.config(state=tk.DISABLED)