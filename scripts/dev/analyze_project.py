# analyze_project.py
import ast
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent
imports_map = defaultdict(set)

for py_file in PROJECT_ROOT.rglob('*.py'):
    if '__pycache__' in str(py_file):
        continue
    try:
        with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
            tree = ast.parse(f.read())
        
        rel_path = py_file.relative_to(PROJECT_ROOT)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imports_map[str(rel_path)].add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports_map[str(rel_path)].add(alias.name)
    except:
        pass

# 출력
print("=== 프로젝트 임포트 맵 ===\n")
for module, deps in sorted(imports_map.items()):
    if 'app' in module or 'scanner' in module or 'core' in module:
        print(f"{module}:")
        for dep in sorted(deps):
            if not dep.startswith('_') and len(dep) > 2:
                print(f"  → {dep}")
        print()

# JSON으로 저장
import json
with open('dependency_map.json', 'w', encoding='utf-8') as f:
    json.dump({k: list(v) for k, v in imports_map.items()}, f, indent=2, ensure_ascii=False)
