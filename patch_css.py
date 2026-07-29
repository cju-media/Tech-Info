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
        }"""

content = re.sub(r'        \.mermaidTooltip \{\n            z-index: 9999 !important;\n        \}', new_css, content)

with open('dashboard/index.html', 'w') as f:
    f.write(content)
