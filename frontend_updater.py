import re
import os

def update_file(filepath, replacements):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
        else:
            print(f"Warning: could not find '{old[:50]}...' in {filepath}")
            
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

print("Script ready.")
