# make_light_context.py - 새 세션 부트스트랩용 구조 개요 생성기
import os

OUTPUT_FILE = "project_context_light.txt"
EXCLUDE_DIRS = {'.git', '__pycache__', '.pytest_cache', '.mypy_cache', 'venv', '.venv', 'logs', 'fonts'}
MAX_LINES_PER_FILE = 15

def make_light(root_dir='.'):
    lines = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for file in sorted(files):
            if not file.endswith('.py'):
                continue
            filepath = os.path.join(root, file)
            rel = os.path.relpath(filepath, root_dir).replace('\\', '/')
            try:
                with open(filepath, encoding='utf-8', errors='ignore') as f:
                    head = [next(f, '') for _ in range(MAX_LINES_PER_FILE)]
                lines.append(f"\n--- {rel} ---")
                lines.extend(l.rstrip() for l in head)
            except Exception:
                pass
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    size = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"완료: {OUTPUT_FILE} ({size:.1f} KB)")

if __name__ == '__main__':
    make_light()
