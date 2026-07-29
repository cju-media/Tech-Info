import re

with open('dashboard/index.html', 'r') as f:
    content = f.read()

# Replace the simple z-index block with the expanded styling
new_css = """        .mermaidTooltip {
            z-index: 9999 !important;
            background-color: #333 !important;
            color: #fff !important;
            padding: 8px !important;
            border-radius: 4px !important;
            font-size: 14px !important;
            opacity: 1 !important;
            display: block !important;
        }"""

content = re.sub(r'        \.mermaidTooltip \{\n            z-index: 9999 !important;\n            background-color: #333 !important;\n            color: #fff !important;\n            padding: 8px !important;\n            border-radius: 4px !important;\n            font-size: 14px !important;\n            opacity: 1 !important;\n            display: block !important;\n            z-index: 9999 !important;\n        \}', new_css, content)

with open('dashboard/index.html', 'w') as f:
    f.write(content)
